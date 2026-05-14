/**
 * SalesPro Next Gen SaaS UI Engine
 */

document.addEventListener('DOMContentLoaded', () => {
    // 1. Initialize Skeleton Loaders Simulation
    const skeletons = document.getElementById('skeleton-loader');
    const mainContent = document.getElementById('main-content');
    
    if (skeletons && mainContent) {
        setTimeout(() => {
            skeletons.style.display = 'none';
            mainContent.style.display = 'block';
            runCountUp();
        }, 800);
    } else {
        runCountUp();
    }

    // 2. Count-up Animation for KPI Values
    function runCountUp() {
        const counters = document.querySelectorAll('.counter');
        const speed = 120;

        counters.forEach(counter => {
            const updateCount = () => {
                const target = +counter.getAttribute('data-target');
                const currentText = counter.innerText.replace(/,/g, '');
                const count = +currentText;
                const inc = Math.max(1, Math.floor(target / speed));

                if (count < target) {
                    counter.innerText = (count + inc).toLocaleString();
                    setTimeout(updateCount, 20);
                } else {
                    counter.innerText = target.toLocaleString();
                }
            };
            updateCount();
        });
    }

    // 3. Command Palette (Cmd + K) Logic
    const cp = document.getElementById('command-palette');
    const cpOverlay = document.getElementById('cp-overlay');
    const cpInput = document.getElementById('cp-input');
    const cpResults = document.getElementById('cp-results');

    const navigationActions = [
        { name: 'ダッシュボードを開く', url: '/', icon: 'fa-house-chimney' },
        { name: '顧客ポータルを表示', url: '/customers', icon: 'fa-address-book' },
        { name: '商品カタログを閲覧', url: '/products', icon: 'fa-box-archive' },
        { name: '新規受注を作成', url: '/orders/add', icon: 'fa-square-plus' },
        { name: '取引履歴を確認', url: '/orders', icon: 'fa-receipt' }
    ];

    window.openCommandPalette = function() {
        cp.style.display = 'block';
        cpOverlay.style.display = 'block';
        cpInput.focus();
        renderResults('');
    };

    window.closeCommandPalette = function() {
        cp.style.display = 'none';
        cpOverlay.style.display = 'none';
        cpInput.value = '';
    };

    if (cpInput) {
        cpInput.addEventListener('input', (e) => renderResults(e.target.value));
        
        window.addEventListener('keydown', (e) => {
            if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
                e.preventDefault();
                openCommandPalette();
            }
            if (e.key === 'Escape') closeCommandPalette();
        });

        cpOverlay.addEventListener('click', closeCommandPalette);
    }

    function renderResults(query) {
        cpResults.innerHTML = '';
        const filtered = navigationActions.filter(a => a.name.includes(query));
        
        filtered.forEach(a => {
            const item = document.createElement('div');
            item.style.cssText = 'padding: 12px 16px; border-radius: 10px; cursor: pointer; display: flex; align-items: center; gap: 14px; transition: 0.2s;';
            item.innerHTML = `<i class="fa-solid ${a.icon}" style="color: var(--primary); width: 20px;"></i> <span style="font-weight: 600; font-size: 0.95rem;">${a.name}</span>`;
            item.onmouseover = () => item.style.background = 'rgba(37, 99, 235, 0.08)';
            item.onmouseout = () => item.style.background = 'transparent';
            item.onclick = () => window.location.href = a.url;
            cpResults.appendChild(item);
        });

        if (filtered.length === 0) {
            cpResults.innerHTML = '<p style="text-align: center; padding: 24px; color: var(--text-muted); font-size: 0.85rem;">No results found.</p>';
        }
    }

    // 4. Global UI Utilities
    window.showToast = function(title, msg, isError = false) {
        const toast = document.getElementById('toast');
        if (!toast) return;

        const icon = toast.querySelector('i');
        const titleEl = document.getElementById('toast-title');
        const msgEl = document.getElementById('toast-msg');
        
        if (titleEl) titleEl.innerText = title;
        if (msgEl) msgEl.innerText = msg;
        
        if (icon) {
            icon.className = isError ? "fa-solid fa-circle-xmark" : "fa-solid fa-circle-check";
            icon.style.color = isError ? "var(--danger)" : "var(--success)";
        }
        
        toast.classList.add('show');
        setTimeout(() => toast.classList.remove('show'), 3000);
    };
});
