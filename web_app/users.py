from flask_login import UserMixin
from typing import *


class User(UserMixin):
    def __init__(self, 
                 username: str = "", 
                 password: str = "", 
                 folder: str = "",
                 is_admin: bool = False) -> None:
        self.id = username
        self.password = password
        self.folder = folder
        self.is_admin = is_admin

    def to_dict(self) -> Dict[str, Any]:
        return {
            'username': self.id,
            'password': self.password,
            'folder': self.folder,
            'is_admin': self.is_admin,
        }
    
    @staticmethod
    def from_dict(data: Dict[str, Any]) -> 'User':
        return User(data['username'], 
                    data['password'], 
                    data['folder'],
                    data.get('is_admin', False))