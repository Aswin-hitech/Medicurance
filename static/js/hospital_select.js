document.addEventListener("DOMContentLoaded", function () {
    const hospitalSelect = document.getElementById("hospital-dropdown");
    if (!hospitalSelect) return;

    hospitalSelect.innerHTML = '<option value="" disabled selected>Loading hospitals...</option>';

    fetch("/api/hospitals")
        .then(response => response.json())
        .then(data => {
            hospitalSelect.innerHTML = '<option value="" disabled selected>Select hospital</option>';

            if (!data.length) {
                hospitalSelect.innerHTML = '<option value="" disabled>No hospitals available</option>';
                return;
            }

            data.forEach(hospital => {
                const option = document.createElement("option");
                option.value = hospital.name;
                option.textContent = `${hospital.name} ${hospital.network ? "(In-Network)" : "(Out-of-Network)"}`;
                hospitalSelect.appendChild(option);
            });
        })
        .catch(() => {
            hospitalSelect.innerHTML = '<option value="" disabled>Error loading hospitals</option>';
        });
});
