import os
from flask import Flask
from flask_bootstrap import Bootstrap5


app = Flask(__name__)
app.secret_key = os.urandom(24)

bootstrap = Bootstrap5(app)