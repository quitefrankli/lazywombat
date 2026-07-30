const TAB_IDS = ['logs', 'terminal', 'map'];

let _terminalView = null;
let _mapView = null;

function switchTab(tabName) {
    if (!TAB_IDS.includes(tabName)) tabName = 'logs';

    TAB_IDS.forEach(id => {
        const navTab = document.getElementById(`${id}-nav-tab`);
        if (navTab) navTab.classList.toggle('active', id === tabName);
        const pane = document.getElementById(id);
        if (pane) pane.classList.toggle('active', id === tabName);
    });

    if (history.replaceState) {
        history.replaceState(null, '', `#${tabName}`);
    } else {
        window.location.hash = '#' + tabName;
    }

    if (tabName === 'terminal' && _terminalView) {
        _terminalView.activate();
    }
    if (tabName === 'map' && _mapView) {
        _mapView.activate();
    }
}

document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('[data-dev-tab]').forEach((tab) => {
        tab.addEventListener('click', (event) => {
            event.preventDefault();
            switchTab(tab.dataset.devTab);
        });
    });
    new LogViewer();
    _terminalView = new TerminalView();
    _mapView = new MapView();

    const initial = window.location.hash.replace('#', '') || 'logs';
    switchTab(initial);
});
