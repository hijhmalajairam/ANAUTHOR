import os
import pytz
from datetime import datetime
from google import genai

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'mp4', 'mov'}

def get_ist_time():
    ist = pytz.timezone('Asia/Kolkata')
    return datetime.now(ist)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def check_content_safety(text):
    try:
        api_key = os.getenv('GEMINI_API_KEY')
        if not api_key: return True
        client = genai.Client(api_key=api_key)
        prompt = f"You are a strict content moderator for a free speech platform. Read this text. If it contains severe hate speech, direct violence, or dangerous illegal instructions, respond with ONLY the word 'FLAGGED'. If it is acceptable, respond with ONLY the word 'SAFE'. Text to analyze: '{text}'"
        response = client.models.generate_content(model='gemini-2.5-flash', contents=prompt)
        if 'FLAGGED' in response.text.upper(): return False
        return True
    except Exception as e:
        print(f"!!! AI Safety Error: {str(e)} !!!")
        return True 

def fact_check_content(text):
    try:
        api_key = os.getenv('GEMINI_API_KEY')
        if not api_key: return "[Error] Your GEMINI_API_KEY is missing!", False
        client = genai.Client(api_key=api_key)
        prompt = f"""Analyze the factual claims in this text. You must return your analysis as a strict scale of percentages adding up to 100%, followed by an explanation. 
        Format strictly as: TRUE_PERCENT|FALSE_PERCENT|UNVERIFIABLE_PERCENT|EXPLANATION. Example: '80|0|20|Explanation.' Text: '{text}'"""
        response = client.models.generate_content(model='gemini-2.5-flash', contents=prompt)
        parts = response.text.split('|')
        if len(parts) >= 4:
            true_pct, false_pct, unverifiable_pct, explanation = parts[0].strip(), parts[1].strip(), parts[2].strip(), parts[3].strip()
            formatted_result = f"[Truth Scale: {true_pct}% True | {false_pct}% False | {unverifiable_pct}% Unverifiable] {explanation}"
            return formatted_result, int(false_pct) > 50 
        return "[Error] AI returned a weird format.", False
    except Exception as e:
        return f"[System Crash] Reason: {str(e)}", False
