# 🏥 MediCurance - A Pensioners Claim App

**Smart Claims. Trusted Care.**

MediCurance is an **AI-powered medical reimbursement validation system** designed to automate and streamline healthcare claim processing for government employees and pensioners. The platform integrates **Flask, MongoDB Atlas, Cloudinary, and RAG-based LLMs** to intelligently analyze medical bills and assist in decision-making.

---

## 🚀 Project Overview

MediCurance enables users to:

- Submit medical reimbursement claims
- Upload medical bills and documents
- Automatically extract and analyze bill data
- Validate claims using AI + government rules (RAG)
- Generate reimbursement insights
- Allow officers to review and approve/reject claims

The system ensures **transparency, efficiency, and accuracy** by combining **AI validation with human-in-the-loop review**.

---

## 🧠 Key Features

### 👤 User Features
- Secure login using OTP / password
- Submit medical claims with document upload
- View claim status and history
- AI-assisted claim validation

### 🏢 Officer Features
- View all submitted claims
- Review AI-generated validation results
- Approve or reject claims

### 🛠️ Admin Features
- Manage hospitals (add/update/remove)
- Assign roles (user, officer, admin)
- Monitor system data and claims

---

## 🤖 AI & RAG Capabilities

- Extracts text from medical bills (PDF/Image)
- Uses **RAG (Retrieval-Augmented Generation)** to compare claims with government annexure rules
- Generates structured output:

```
Eligibility: Eligible / Not Eligible
Confidence Score:
Reason:
```

---

## 🏗️ Tech Stack

| Layer         | Technology              |
| ------------- | ----------------------- |
| Backend       | Flask (Python)          |
| Database      | MongoDB Atlas           |
| Cloud Storage | Cloudinary              |
| AI / LLM      | Groq API                |
| RAG           | LangChain + ChromaDB    |
| OCR / Parsing | pdfplumber, pytesseract |
| Frontend      | HTML, CSS, JavaScript   |

---

## 📂 Project Structure

```
MediCurance/
│
├── app.py
├── requirements.txt
├── .env
│
├── backend/
├── services/
├── database/
├── utils/
├── templates/
├── static/
│
├── resources/
│   └── annexures/
│
├── vectorstore/
```

---

## 🔄 System Workflow

```
User Login
   ↓
Submit Claim
   ↓
Upload Medical Bill
   ↓
Text Extraction (PDF/OCR)
   ↓
RAG-based AI Validation
   ↓
Store Claim in MongoDB
   ↓
Officer Review
   ↓
Approve / Reject
```

---

## ⚙️ Installation & Setup

### 1️⃣ Clone the Repository

```bash
git clone https://github.com/your-username/medicurance.git
cd medicurance
```

### 2️⃣ Create Virtual Environment

```bash
python -m venv venv
# On Windows:
venv\Scripts\activate
# On Mac/Linux:
source venv/bin/activate
```

### 3️⃣ Install Dependencies

```bash
pip install -r requirements.txt
```

### 4️⃣ Configure Environment Variables

Create a `.env` file:

```env
SECRET_KEY=your_secret_key
MONGO_URI=your_mongodb_uri

CLOUDINARY_CLOUD_NAME=your_cloud_name
CLOUDINARY_API_KEY=your_api_key
CLOUDINARY_API_SECRET=your_api_secret

GROQ_API_KEY=your_groq_api_key
```

### 5️⃣ Build RAG Vector Database

```bash
python build_vector.py
```

### 6️⃣ Run the Application

```bash
python app.py
```

---

## 📡 API Endpoints (Sample)

| Endpoint         | Description       |
| ---------------- | ----------------- |
| `/send_otp`      | Send OTP          |
| `/verify_otp`    | Verify OTP        |
| `/submit_claim`  | Submit claim      |
| `/claim_status`  | View user claims  |
| `/officer`       | Officer dashboard |
| `/api/hospitals` | Get hospital list |

---

## 🔐 Security Features

- Password hashing using bcrypt
- Role-based access control (RBAC)
- Secure file uploads
- Environment variable protection
- Session-based authentication

---

## 📦 Future Enhancements

- 🔍 Fraud detection using AI
- 📊 Government analytics dashboard
- 📱 Mobile app integration
- 🤖 Multi-agent AI system
- 🔗 Real-time hospital verification APIs

---

## 🧪 Sample Output

```
Eligibility: Eligible
Confidence Score: 92%
Reason: Treatment is covered and hospital is in approved network.
```

---

## 👨‍💻 Author

**Aswin**  
kit28.24bam009@gmail.com
KIT-KalaignarKarunanidhi Institute of Technology, Coimbatore
AI/ML Student

---

## 📜 License

This project is for educational and research purposes.

---

## ⭐ Acknowledgements

- Flask Community
- MongoDB Atlas
- LangChain & ChromaDB
- Groq AI
- Cloudinary

---

## 💡 Tagline

**MediCurance – A Pensioners Claim App**
