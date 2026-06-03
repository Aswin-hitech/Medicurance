from services.llm_service import ask_llm


def ai_verify(text):

    prompt = f"""
You are an AI system validating government medical reimbursement claims.

Analyze the following bill text.

{text}

Return structured output:

Eligibility: Eligible / Not Eligible
Confidence Score:
Reason:
"""

    return ask_llm(prompt)