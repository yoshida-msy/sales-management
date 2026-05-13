/**
 * SalesPro Premium SaaS UI Components
 */

document.addEventListener('DOMContentLoaded', () => {
    // Count-up Animation for KPI Cards
    const counters = document.querySelectorAll('.counter');
    const speed = 200;

    counters.forEach(counter => {
        const updateCount = () => {
            const target = +counter.getAttribute('data-target');
            const count = +counter.innerText.replace(/,/g, '');
            const inc = target / speed;

            if (count < target) {
                counter.innerText = Math.ceil(count + inc).toLocaleString();
                setTimeout(updateCount, 1);
            } else {
                counter.innerText = target.toLocaleString();
            }
        };
        updateCount();
    });

    // Sidebar Active State Auto-scrolling
    const activeItem = document.querySelector('.sidebar-nav li.active');
    if (activeItem) {
        activeItem.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
});
