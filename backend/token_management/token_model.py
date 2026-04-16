from pydantic import BaseModel


class TokenRequest(BaseModel):
    client_id: str
    access_token: str
    backend: str

class ManualTokenOnly(BaseModel):
    access_token: str    