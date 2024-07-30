import os
import requests
import json
from flask import Flask, request, render_template, jsonify, redirect, url_for, flash
import qrcode
from io import BytesIO
from qrcode.image.styledpil import StyledPilImage
from qrcode.image.styles.moduledrawers import RoundedModuleDrawer
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
from dotenv import load_dotenv
from pymongo import MongoClient
from bson import ObjectId

# Load environment variables
load_dotenv()

app = Flask(__name__)
application=app
app.secret_key = os.getenv('SECRET_KEY') or os.urandom(24)

# MongoDB connection setup
MONGODB_URI = os.getenv('MONGODB_URI')
client = MongoClient(MONGODB_URI)
db = client['eventdb']
registration_collection = db['registration']
validation_collection = db['validation']

# Google Apps Script Web App URL
REGISTRATION_SCRIPT_URL = os.getenv('REGISTRATION_SCRIPT_URL')
VALIDATION_SCRIPT_URL = os.getenv('VALIDATION_SCRIPT_URL')

# Custom JSON encoder for ObjectId
class ObjectIdEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, ObjectId):
            return str(obj)
        return json.JSONEncoder.default(self, obj)

def generate_qr_code(data):
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4,
    )
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white", image_factory=StyledPilImage, module_drawer=RoundedModuleDrawer())
    return img

def send_email_with_qr(name, register_number, recipient_email, qr_image):
    smtp_server = 'smtp.gmail.com'
    smtp_port = 587
    smtp_user = os.getenv('SMTP_USER')
    smtp_password = os.getenv('SMTP_PASSWORD')

    subject = 'SIST ACM SIGAI Student Chapter - EVENT CONFIRMATION'
    body = render_template('email.html', name=name, register_number=register_number, email=recipient_email)

    message = MIMEMultipart()
    message['From'] = smtp_user
    message['To'] = recipient_email
    message['Subject'] = subject

    message.attach(MIMEText(body, 'html'))

    qr_image_io = BytesIO()
    qr_image.save(qr_image_io, 'PNG')
    qr_image_io.seek(0)
    qr_attachment = MIMEImage(qr_image_io.read(), name='qr_code.png')
    message.attach(qr_attachment)

    try:
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.send_message(message)
    except Exception as e:
        print(f"Failed to send email: {e}")
        flash('Failed to send email with QR code. Please contact the event organizers.', 'error')

@app.route('/', methods=['GET'])
def index():
    return render_template('registration_form.html')

@app.route('/register', methods=['POST'])
def register():
    # Collect attendee information
    attendee_info = {
        "name": request.form['name'],
        "degree": request.form['degree'],
        "class_section": request.form['class_section'],
        "year": request.form['year'],
        "register_number": request.form['register_number'],
        "email": request.form['email'],
        "phone": request.form['phone'],
    }

    # Check if registration number already exists
    existing_registration = registration_collection.find_one({"register_number": attendee_info['register_number']})
    if existing_registration:
        flash('You have already registered. Please check your details and try again.', 'error')
        return redirect(url_for('index'))

    # Insert the new registration
    result = registration_collection.insert_one(attendee_info)
    
    # Add ObjectId to attendee_info for tracking
    attendee_info["_id"] = result.inserted_id

    # Prepare data for Google Apps Script
    json_data = json.dumps(attendee_info, cls=ObjectIdEncoder)
    
    # Send data to Google Apps Script
    response = requests.post(REGISTRATION_SCRIPT_URL, data=json_data, headers={"Content-Type": "application/json"})
    if response.status_code != 200:
        flash('Failed to save data to Google Sheets. Please try again later.', 'error')
        return redirect(url_for('index'))
    
    # Generate QR code
    qr_img = generate_qr_code(json_data)

    # Send email with QR code
    send_email_with_qr(attendee_info['name'], attendee_info['register_number'], attendee_info['email'], qr_img)

    return render_template('thanks.html')

@app.route('/$i$tvali', methods=['GET', 'POST'])
def validate():
    if request.method == 'GET':
        return render_template('validation.html')

    qr_data = request.json.get('qr_data')
    if not qr_data:
        return jsonify({'valid': False, 'message': 'No QR data provided.'})

    try:
        # Decode the JSON data from QR code
        attendee_info = json.loads(qr_data)

        # Extract register number and other info
        register_number = attendee_info.get('register_number')

        # Check if the necessary fields are present
        required_fields = ['name', 'degree', 'class_section', 'year', 'register_number', 'email', 'phone']
        if not all(field in attendee_info for field in required_fields):
            return jsonify({'valid': False, 'message': 'Missing required information in QR code.'})

        # Check if registration number exists in the registration collection
        existing_registration = registration_collection.find_one({"register_number": register_number})
        if not existing_registration:
            return jsonify({'valid': False, 'message': 'Registration not found. Please register first.'})

        # Check if QR code has already been scanned
        existing_validation = validation_collection.find_one({"register_number": register_number})
        if existing_validation:
            return jsonify({'valid': False, 'message': 'This QR code has already been scanned.'})

        # Prepare validation data (matching registration format)
        validation_data = {
            "name": attendee_info['name'],
            "degree": attendee_info['degree'],
            "class_section": attendee_info['class_section'],
            "year": attendee_info['year'],
            "register_number": attendee_info['register_number'],
            "email": attendee_info['email'],
            "phone": attendee_info['phone']
        }

        # Insert the validation data
        result = validation_collection.insert_one(validation_data)
        validation_data["_id"] = str(result.inserted_id)

        # Prepare data for Google Apps Script
        json_data = json.dumps(validation_data, cls=ObjectIdEncoder)
        
        # Send data to Google Apps Script
        response = requests.post(VALIDATION_SCRIPT_URL, data=json_data, headers={"Content-Type": "application/json"})
        if response.status_code != 200:
            return jsonify({'valid': False, 'message': 'Failed to save data to Google Sheets.'})

        return jsonify({'valid': True, 'attendee': validation_data, 'message': 'Validation successful. Data saved.'})
    
    except json.JSONDecodeError:
        return jsonify({'valid': False, 'message': 'Invalid QR code format.'})
    except KeyError:
        return jsonify({'valid': False, 'message': 'Missing required information in QR code.'})
    
if __name__ == '__main__':
    app.run(debug=True)
