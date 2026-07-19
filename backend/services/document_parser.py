import pdfplumber

def extract_text(file_path):

    text = ""

    try:
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                content = page.extract_text()
                if content:
                    text += content

    except Exception as e:
        print("PDF parse error:", e)

    return text