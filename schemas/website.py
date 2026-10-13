
from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class WebsiteCreate(BaseModel):
    url: HttpUrl
    name: str | None = Field(default=None, max_length=200)


class WebsiteResponse(BaseModel):
    id: int
    url: str
    name: str | None
    user_id: int

    model_config = ConfigDict(from_attributes=True)