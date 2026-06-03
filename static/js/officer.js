function approveClaim(id) {
    if (confirm('Are you sure you want to approve this claim?')) {
        showLoading();
        fetch('/approve/' + id, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            }
        })
        .then(response => response.json())
        .then(data => {
            showAlert('Claim approved successfully!', 'success');
            setTimeout(() => {
                location.reload();
            }, 1500);
        })
        .catch(error => {
            showAlert('Error approving claim. Please try again.', 'error');
        });
    }
}

function rejectClaim(id) {
    if (confirm('Are you sure you want to reject this claim?')) {
        const reason = prompt('Please provide rejection reason:');
        if (reason !== null) {
            showLoading();
            fetch('/reject/' + id, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ reason: reason })
            })
            .then(response => response.json())
            .then(data => {
                showAlert('Claim rejected successfully!', 'success');
                setTimeout(() => {
                    location.reload();
                }, 1500);
            })
            .catch(error => {
                showAlert('Error rejecting claim. Please try again.', 'error');
            });
        }
    }
}

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
}