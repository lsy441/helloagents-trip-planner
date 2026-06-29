"""Pexels图片服务 (替换已废弃的Unsplash API)"""

import requests
from typing import List, Optional
from ..config import get_settings

class PexelsService:
    """Pexels图片服务类"""

    def __init__(self):
        settings = get_settings()
        self.api_key = settings.unsplash_access_key
        self.base_url = "https://api.pexels.com/v1"

    def search_photos(self, query: str, per_page: int = 5) -> List[dict]:
        try:
            url = f"{self.base_url}/search"
            headers = {"Authorization": self.api_key}
            params = {
                "query": query,
                "per_page": per_page,
                "orientation": "landscape",
            }

            response = requests.get(url, headers=headers, params=params, timeout=5)
            response.raise_for_status()

            data = response.json()
            photos_list = data.get("photos", [])

            photos = []
            for photo in photos_list:
                src = photo.get("src", {})
                photos.append({
                    "id": photo.get("id"),
                    "url": src.get("large") or src.get("original"),
                    "thumb": src.get("medium") or src.get("small"),
                    "description": photo.get("alt") or query,
                    "photographer": photo.get("photographer", ""),
                })

            return photos

        except Exception as e:
            print(f"❌ Pexels搜索失败: {str(e)}")
            return []

    def get_photo_url(self, query: str) -> Optional[str]:
        photos = self.search_photos(query, per_page=1)
        if photos:
            return photos[0].get("url")
        return None


_pexels_service = None

def get_unsplash_service() -> PexelsService:
    global _pexels_service
    if _pexels_service is None:
        _pexels_service = PexelsService()
    return _pexels_service
