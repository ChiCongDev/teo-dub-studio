"""Download sources, in one fixed failover order: Douyin's own page first, then the public download sites."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Provider:
    id: str
    name: str
    url: str


PROVIDERS = (
    Provider('douyin', 'Douyin trực tiếp', 'https://www.douyin.com/'),
    Provider('savetik', 'SaveTik', 'https://savetik.io/en/douyin-video-downloader'),
    Provider('vienphanmem', 'Viện Phần Mềm', 'https://vienphanmem.com/snaptik/'),
    Provider('socialvideo', 'Social Video Download', 'https://socialvideodownload.com/douyin'),
    Provider('anysave', 'AnySave', 'https://any-save.com/douyin'),
)


def provider_name(provider_id):
    if provider_id == 'local_cache':
        return 'Video đã lưu'
    if provider_id == 'direct':
        return 'TikTok trực tiếp'
    return next((item.name for item in PROVIDERS if item.id == provider_id), PROVIDERS[0].name)


def provider_url(provider_id):
    return next((item.url for item in PROVIDERS if item.id == provider_id), PROVIDERS[0].url)
