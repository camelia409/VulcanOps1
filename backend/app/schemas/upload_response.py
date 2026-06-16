from pydantic import BaseModel


class UploadResponse(BaseModel):
    status: str
    rows_processed: int = 0
    rows_accepted: int = 0
    rows_rejected: int = 0
    errors: list[str] = []
