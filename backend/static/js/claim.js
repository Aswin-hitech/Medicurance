function validateClaim() {
    const name = document.getElementById('name').value.trim();
    const hospital = document.getElementById('hospital').value.trim();
    const amount = document.getElementById('amount').value;
    const bill = document.getElementById('bill').files[0];
    
    if (name === '') {
        alert('Please enter patient name');
        return false;
    }
    
    if (hospital === '') {
        alert('Please enter hospital name');
        return false;
    }
    
    if (amount <= 0) {
        alert('Please enter a valid amount');
        return false;
    }
    
    if (!bill) {
        alert('Please upload medical bill');
        return false;
    }
    
    // Check file size (5MB limit)
    if (bill.size > 5 * 1024 * 1024) {
        alert('File size should not exceed 5MB');
        return false;
    }
    
    // Check file type
    const allowedTypes = ['application/pdf', 'image/jpeg', 'image/jpg', 'image/png'];
    if (!allowedTypes.includes(bill.type)) {
        alert('Please upload PDF or image files only');
        return false;
    }
    
    return true;
}

// File upload preview
document.getElementById('bill')?.addEventListener('change', function(e) {
    const fileName = e.target.files[0]?.name;
    const label = document.querySelector('.file-upload-label span');
    if (label && fileName) {
        label.textContent = fileName;
    }
});