from flask import Flask
import os
from dotenv import load_dotenv

# Import our new separate parts
from blueprints.auth import auth_bp
from blueprints.pages import pages_bp
from blueprints.actions import actions_bp

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY')

# Set up global configs
app.config['UPLOAD_FOLDER'] = 'static/uploads'
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True) 

# Plug the blueprints into the main engine
app.register_blueprint(auth_bp)
app.register_blueprint(pages_bp)
app.register_blueprint(actions_bp)

if __name__ == '__main__':
    app.run(debug=True)
