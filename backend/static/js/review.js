document.addEventListener('DOMContentLoaded', function() {
    const submitBtn = document.getElementById('submitClaim');
    const editBtn = document.getElementById('editClaim');
    const escalateBtn = document.getElementById('escalateClaim');
    const aiResult = document.getElementById('ai_result');

    // Check AI result
    if (aiResult) {
        const resultText = aiResult.innerText.toLowerCase();
        if (resultText.includes('not eligible') || resultText.includes('invalid')) {
            showAlert('⚠ AI suggests this claim may not be eligible. You can still escalate to officer review.', 'warning');
        }
    }

    // Submit Claim
    if (submitBtn) {
        submitBtn.addEventListener('click', function(e) {
            e.preventDefault();
            if (confirm('Are you sure you want to submit this claim?')) {
                showLoading();
                // Simulate API call
                setTimeout(() => {
                    window.location.href = '/claim_status';
                }, 1500);
            }
        });
    }

    // Edit Claim
    if (editBtn) {
        editBtn.addEventListener('click', function() {
            window.location.href = '/claim_request';
        });
    }

    // Escalate to Officer
    if (escalateBtn) {
        escalateBtn.addEventListener('click', function() {
            if (confirm('Send this claim directly to officer review?')) {
                showLoading();
                fetch('/escalate_claim', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    }
                })
                .then(response => response.json())
                .then(data => {
                    showAlert('Claim escalated to officer successfully!', 'success');
                    setTimeout(() => {
                        window.location.href = '/dashboard';
                    }, 2000);
                })
                .catch(error => {
                    showAlert('Error escalating claim. Please try again.', 'error');
                });
            }
        });
    }
});

function showAlert(message, type) {
    const alertDiv = document.createElement('div');
    alertDiv.className = `alert alert-${type}`;
    alertDiv.innerHTML = message;
    document.querySelector('.container').insertBefore(alertDiv, document.querySelector('.glassy-card'));
    
    setTimeout(() => {
        alertDiv.remove();
    }, 5000);
}

function showLoading() {
    const overlay = document.createElement('div');
    overlay.style.cssText = `
        position: fixed;
        top: 0;
        left: 0;
        width: 100%;
        height: 100%;
        background: rgba(0,0,0,0.5);
        display: flex;
        justify-content: center;
        align-items: center;
        z-index: 9999;
    `;
    overlay.innerHTML = '<div class="spinner"></div>';
    document.body.appendChild(overlay);
    
    setTimeout(() => {
        overlay.remove();
    }, 5000);
}