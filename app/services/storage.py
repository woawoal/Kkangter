"""이미지 저장. 업로드 시 서버에서 리사이즈(긴 변 1280px, WebP)해 용량을 1/5 이하로 줄인다.
local: data/uploads (개발용) / s3: Cloudflare R2, Supabase Storage 등 S3 호환 스토리지."""
import io
import uuid

from PIL import Image, ImageOps

from ..config import settings

MAX_SIDE = 1280


class InvalidImage(ValueError):
    pass


def _process(raw: bytes) -> bytes:
    if len(raw) > settings.MAX_UPLOAD_BYTES:
        raise InvalidImage("사진은 8MB 이하만 올릴 수 있어요")
    try:
        img = Image.open(io.BytesIO(raw))
        img = ImageOps.exif_transpose(img)  # 폰 사진 회전 보정 + EXIF(위치정보) 제거
    except Exception as e:  # noqa: BLE001
        raise InvalidImage("이미지 파일이 아니에요") from e
    img = img.convert("RGB")
    img.thumbnail((MAX_SIDE, MAX_SIDE))
    out = io.BytesIO()
    img.save(out, "WEBP", quality=82)
    return out.getvalue()


def save_image(raw: bytes) -> str:
    data = _process(raw)
    key = f"posts/{uuid.uuid4().hex}.webp"
    if settings.STORAGE_BACKEND == "s3":
        import boto3

        s3 = boto3.client(
            "s3",
            endpoint_url=settings.S3_ENDPOINT,
            aws_access_key_id=settings.S3_ACCESS_KEY,
            aws_secret_access_key=settings.S3_SECRET_KEY,
        )
        s3.put_object(Bucket=settings.S3_BUCKET, Key=key, Body=data, ContentType="image/webp",
                      CacheControl="public, max-age=31536000, immutable")
        return f"{settings.S3_PUBLIC_BASE.rstrip('/')}/{key}"
    path = settings.UPLOAD_DIR / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return f"/uploads/{key}"
