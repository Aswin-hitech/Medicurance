function validateLogin() {
    const mobile = document.getElementById('mobile').value;
    const password = document.getElementById('password').value;
    
    if (mobile.length !== 10 || !/^\d+$/.test(mobile)) {
        alert('Please enter a valid 10-digit mobile number');
        return false;
    }
    
    if (password.length < 6) {
        alert('Password must be at least 6 characters');
        return false;
    }
    
    return true;
}

// Add input formatting
document.getElementById('mobile')?.addEventListener('input', function(e) {
    this.value = this.value.replace(/[^0-9]/g, '').slice(0, 10);
});