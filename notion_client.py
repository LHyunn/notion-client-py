import requests
import json
import datetime
from pprint import pprint
import os
import mimetypes

class NotionClient():
    def __init__(self, notion_database_id, notion_key, notion_version="2022-06-28"):
        self.notion_database_id = notion_database_id
        self.notion_key = notion_key
        # API 엔드포인트 URL들을 속성으로 정의
        self.api_base_url = "https://api.notion.com/v1"
        self.database_url = f"{self.api_base_url}/databases/{self.notion_database_id}/query"
        self.pages_url = f"{self.api_base_url}/pages"
        self.files_url = f"{self.api_base_url}/file_uploads" # 파일 업로드를 위한 새 엔드포인트
        
        self.headers = {
            "accept": "application/json",
            "Authorization": f"Bearer {self.notion_key}",
            "content-type": "application/json",
            "Notion-Version": notion_version,
        }
        self._property_dict = None
        self._page_form = None

    @property
    def property_dict(self):
        if self._property_dict is None:
            self._property_dict = self._get_properties()
        return self._property_dict

    @property
    def page_form(self):
        if self._page_form is None:
            self._page_form = self._create_page_form()
        return self._page_form

    def transform_date(self, date_str):
        dt_object = datetime.datetime.strptime(date_str, "%Y%m%d%H%M%S")
        return dt_object.astimezone().isoformat()
        
    def print_property_dict(self):
        pprint(self.property_dict)
        
    def print_page_form(self):
        pprint(self.page_form)
        
    def _get_properties(self):
        payload = { "page_size": 1 }
        try:
            response = requests.post(self.database_url, json=payload, headers={**self.headers, "Content-Type": "application/json"})
            response.raise_for_status()
        except requests.exceptions.RequestException as e:
            raise ConnectionError(f"Failed to connect to Notion API: {e}")

        results = response.json().get("results")
        if not results:
            raise ValueError("The Notion database is empty. Please add at least one page.")
            
        properties = results[0].get("properties", {})
        return {prop_name: prop_data["type"] for prop_name, prop_data in properties.items()}
    
    def _create_page_form(self):
        page_data = {"parent": {"database_id": self.notion_database_id}, "properties": {}}
        for key, prop_type in self.property_dict.items():
            if prop_type in ["select", "status"]:
                page_data["properties"][key] = {prop_type: {"name": ""}}
            elif prop_type == "number":
                 page_data["properties"][key] = {"number": None}
            elif prop_type == "date":
                page_data["properties"][key] = {"date": {"start": "", "end": None}}
            elif prop_type in ["rich_text", "title"]:
                page_data["properties"][key] = {prop_type: [{"type": "text", "text": {"content": ""}}]}
            elif prop_type == "files":
                page_data["properties"][key] = {"files": []}
        return page_data

    def _upload_local_file(self, file_path):
        """로컬 파일을 Notion에 업로드하고 파일 URL을 반환하는 내부 헬퍼 함수."""
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found at path: {file_path}")

        file_name = os.path.basename(file_path)
        content_type, _ = mimetypes.guess_type(file_name)
        if content_type is None:
            content_type = "application/octet-stream"

        # 1단계: Notion API에 업로드 요청을 보내 임시 업로드 URL 받기
        files_payload = {"filename": file_name, "content_type": content_type}
        try:
            res = requests.post(self.files_url, json=files_payload, headers={**self.headers, "Content-Type": "application/json"})
            res.raise_for_status()
            upload_data = res.json()
            upload_url = upload_data["upload_url"]
            file_id = upload_data["id"]
        except requests.exceptions.RequestException as e:
            print(f"Error getting upload URL from Notion: {e}")
            if e.response: print(f"Response: {e.response.text}")
            return None

        # 2단계: 받은 임시 URL로 실제 파일 데이터 업로드
        with open(file_path, 'rb') as f:
            files = {
                'file': (file_name, f, content_type)
            }
        
            try:
                res = requests.post(
                    upload_url,
                    headers={"Authorization": f"Bearer {self.notion_key}",
                             "Notion-Version": "2022-06-28"},
                    files=files
                )
                res.raise_for_status()
            except requests.exceptions.RequestException as e:
                print(f"Error uploading file to temporary URL: {e}")
                if e.response: print(f"Response: {e.response.text}")
                return None
        
        print(f"Successfully uploaded local file: {file_name}")
        return res.json().get("id")  # Notion에서 접근할 수 있는 파일 ID 반환

    def _get_file_payload(self, value):
        """URL인지 로컬 경로인지 확인하여 적절한 파일 페이로드를 생성합니다."""
        if isinstance(value, str) and value.startswith(('http://', 'https://')):
            return {"type": "external", "external": {"url": value}}
        elif isinstance(value, str) and os.path.exists(value):
            notion_url = self._upload_local_file(value)
            if notion_url:
                return {"type": "file", "file": {"url": notion_url}}
        else:
            print(f"Warning: Invalid file source: {value}. Skipping.")
            return None
        
    def _add_image_block_to_page(self, page_id, image_id):
        url = f"https://api.notion.com/v1/blocks/{page_id}/children"
        payload = {
            "children": [
                {
                    "object": "block",
                    "type": "image",
                    "image": {
                        "type": "file_upload",
                        "file_upload": {
                            "id": image_id
                        }
                    }
                }
            ]
        }
        try:
            res = requests.patch(url, headers={
                **self.headers
            }, data=json.dumps(payload))
            res.raise_for_status()
            return True
        except requests.exceptions.RequestException as e:
            print(f"Error adding image block to page: {e}")
            if e.response: print(f"Response: {e.response.text}")
            return False

    def create_page(self, page_values, image_file_path=None):
        upload_data = json.loads(json.dumps(self.page_form))

        # 아이콘 및 커버 처리
        for key in ["__icon", "__cover"]:
            if key in page_values:
                payload = self._get_file_payload(page_values[key])
                if payload:
                    upload_data[key.strip('_')] = payload

        # 속성 처리
        for key, value in page_values.items():
            if key.startswith("__") or key not in self.property_dict:
                continue

            prop_type = self.property_dict[key]
            if prop_type in ["formula", "created_time", "last_edited_time"]:
                continue

            if prop_type in ["select", "status"]:
                upload_data["properties"][key][prop_type]["name"] = value
            elif prop_type == "date":
                upload_data["properties"][key][prop_type]["start"] = value
            elif prop_type in ["rich_text", "title"]:
                upload_data["properties"][key][prop_type][0]["text"]["content"] = value
            elif prop_type == "number":
                upload_data["properties"][key][prop_type] = value
        
        try:
            res = requests.post(self.pages_url, headers={**self.headers}, json=upload_data)
            res.raise_for_status()
            print("Successfully created the page in Notion.")
            page_id = res.json().get("id")

            if image_file_path and page_id:
                image_paths = []
                if isinstance(image_file_path, str):
                    image_paths = [image_file_path]
                elif isinstance(image_file_path, list):
                    image_paths = image_file_path
                else:
                    print(f"Warning: Unsupported type for image_file_path: {type(image_file_path)}. Skipping image upload.")

                for path in image_paths:
                    image_id = self._upload_local_file(path)
                    if image_id:
                        success = self._add_image_block_to_page(page_id, image_id)
                        if success:
                            print(f"Successfully added image block for: {os.path.basename(path)}")
                        else:
                            print(f"Failed to add image block for: {os.path.basename(path)}")
                    else:
                        print(f"Failed to upload image file: {path}")
            return True
        except requests.exceptions.RequestException as e:
            print(f"Failed to create page in Notion: {e}")
            if e.response: print(f"Response body: {e.response.text}")
            return None
