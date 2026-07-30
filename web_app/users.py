import hmac
from typing import *

from flask_login import UserMixin
from pydantic import BaseModel, ConfigDict, Field, RootModel
from werkzeug.security import check_password_hash, generate_password_hash

from web_app.config import ConfigManager


class User(UserMixin, BaseModel):
    # populate_by_name: accept `username=` in code; serialize_by_alias: keep the
    # on-disk key `username` (the field is named `id` for flask_login's get_id).
    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    id: str = Field(default="", alias="username")
    password: str = ""
    folder: str = ""
    is_admin: bool = False
    is_elevated: bool = False

    def __init__(self,
                 username: str = "",
                 password: str = "",
                 folder: str = "",
                 is_admin: bool = False,
                 is_elevated: bool = False) -> None:
        # Preserve the historical positional signature used across the codebase.
        super().__init__(username=username, password=password, folder=folder,
                         is_admin=is_admin, is_elevated=is_elevated)

    @classmethod
    def create(cls,
               username: str,
               password: str,
               folder: str,
               is_admin: bool = False,
               is_elevated: bool = False) -> 'User':
        user = cls(
            username=username,
            folder=folder,
            is_admin=is_admin,
            is_elevated=is_elevated,
        )
        user.set_password(password)
        return user

    def set_password(self, password: str) -> None:
        config = ConfigManager()
        password_hash = generate_password_hash(
            password,
            method=config.password_hash_method,
        )
        self.password = f"{config.password_hash_prefix}{password_hash}"

    def verify_password(self, password: str) -> bool:
        config = ConfigManager()
        if self.password.startswith(config.password_hash_prefix):
            password_hash = self.password.removeprefix(
                config.password_hash_prefix
            )
            try:
                return check_password_hash(password_hash, password)
            except ValueError:
                return False

        password_matches = hmac.compare_digest(
            self.password.encode("utf-8"),
            password.encode("utf-8"),
        )
        if password_matches:
            self.set_password(password)
        return password_matches

    def has_elevated_access(self) -> bool:
        return self.is_admin or self.is_elevated

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump(by_alias=True)

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> 'User':
        return User.model_validate(data)

    def __repr__(self):
        return f"User(username={self.id}, folder={self.folder}, is_admin={self.is_admin}, is_elevated={self.is_elevated})"


class UsersFile(RootModel):
    """Transactional model for users.json (a JSON list of users).

    Provides dict-style access keyed by username so callers read like the old
    ``load_users()`` dict while the file remains a plain list on disk.
    """
    root: List[User] = []

    def as_dict(self) -> Dict[str, User]:
        return {user.id: user for user in self.root}

    def get(self, username: str) -> Optional[User]:
        return self.as_dict().get(username)

    def __contains__(self, username: str) -> bool:
        return any(user.id == username for user in self.root)

    def add(self, user: User) -> None:
        self.root.append(user)

    def remove(self, username: str) -> None:
        self.root = [user for user in self.root if user.id != username]
