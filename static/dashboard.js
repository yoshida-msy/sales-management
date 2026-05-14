/**
 * SalesPro Next Gen SaaS UI Engine
 */

document.addEventListener('DOMContentLoaded', () => {
    // 1. Initialize Skeleton Loaders
    // In a real app, you'd hide this after AJAX, here we simulate it
    const skeletons = document.querySelectorAll('.skeleton');
    if (skeletons.length > 0) {
        setTimeout(() => {
            document.getElementById('loading-state').style.display = 'none';
            document.getElementById('dashboard-content').style.display = 'block';
            runAnimations();
        }, 600);
    } else {
        runAnimations();
    }

    function runAnimations() {
        // 2. Count-up Animation for KPI Cards
        const counters = document.querySelectorAll('.counter');
        const speed = 100;

        counters.forEach(counter => {
            const updateCount = () => {
                const target = +counter.getAttribute('data-target');
                const currentText = counter.innerText.replace(/,/g, '');
                const count = +currentText;
                const inc = Math.max(1, target / speed);

                if (count < target) {
                    counter.innerText = Math.ceil(count + inc).toLocaleString();
                    setTimeout(updateCount, 15);
                } else {
                    counter.innerText = target.toLocaleString();
                }
            };
            updateCount();
        });
    }

    // 3. Command Palette (Cmd+K) Advanced Logic
    const cp = document.getElementById('command-palette');
    const cpInput = document.getElementById('cp-input');
    const cpResults = document.getElementById('cp-results');

    const actions = [
        { name: 'ダッシュボードへ移動', url: '/', icon: 'fa-chart-pie' },
        { name: '新規受注を作成する', url: '/orders/add', icon: 'fa-square-plus' },
        { name: '商品カタログを見る', url: '/products', icon: 'fa-box-archive' },
        { name: '顧客リストを開く', url: '/customers', icon: 'fa-address-book' },
        { name: 'ログアウト', url: '/logout', icon: 'fa-right-from-bracket' }
    ];

    if (cpInput) {
        cpInput.addEventListener('input', (e) => {
            const val = e.target.value.toLowerCase();
            cpResults.innerHTML = '';
            
            const filtered = actions.filter(a => a.name.toLowerCase().includes(val));
            
            filtered.forEach(a => {
                const div = document.createElement('div');
                div.style.cssText = 'padding: 12px 16px; border-radius: 8px; cursor: pointer; display: flex; align-items: center; gap: 12px; transition: 0.2s;';
                div.innerHTML = `<i class="fa-solid ${a.icon}" style="color: var(--primary); width: 20px;"></i> <span style="font-size: 0.9rem; font-weight: 600;">${a.name}</span>`;
                div.addEventListener('mouseenter', () => div.style.background = 'var(--border)');
                div.addEventListener('mouseleave', () => div.style.background = 'transparent');
                div.addEventListener('click', () => window.location.href = a.url);
                cpResults.appendChild(div);
            });

            if (filtered.length === 0) {
                cpResults.innerHTML = '<p style="text-align: center; padding: 20px; color: var(--text-muted); font-size: 0.8rem;">No results found</p>';
            }
        });
    }

    // 4. Notification Center Logic
    const notifBtn = document.getElementById('notif-btn');
    const notifDropdown = document.getElementById('notif-dropdown');
    if (notifBtn) {
        document.addEventListener('click', (e) => {
            if (!notifBtn.contains(e.target) && !notifDropdown.contains(e.target)) {
                notifDropdown.style.display = 'none';
            }
        });
    }

    // 5. Sidebar Hover Glow Effect (Optional dynamic injection)
    const sidebarLinks = document.querySelectorAll('.sidebar-nav a');
    sidebarLinks.forEach(link => {
        link.addEventListener('mousemove', (e) => {
            const rect = link.getBoundingClientRect();
            const x = e.clientX - rect.left;
            const y = e.clientY - rect.top;
            link.style.setProperty('--x', `${x}px`);
            link.style.setProperty('--y', `${y}px`);
        });
    });
});
