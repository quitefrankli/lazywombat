function toggleGoalState(switchElement, goalId) {
	const state = switchElement.checked;

	const data = {
		"goal_id": goalId,
		"state": state
	};

	fetch("/todoist/goal/toggle_state", {
		method: "POST",
		headers: {
			"Content-Type": "application/json",
			"X-CSRFToken": getTodoistCsrfToken()
		},
		body: JSON.stringify(data)
	})
	.then(response => response.json())
	.then(data => {
		if (data["success"]) {
			console.log("Goal state toggled successfully");
		} else {
			console.error("Failed to toggle goal state");
		}
	})
	.catch((error) => {
		console.error("Failed to toggle goal state", error);
        switchElement.checked = !state; // Revert the switch state on error
	});
}

const goalDragState = {
	item: null,
	startX: 0,
	startY: 0,
	timer: null,
	active: false,
	ghost: null,
	target: null,
	targetParentId: undefined,
	hoverTimer: null,
	suppressClick: false,
	holdMs: 0,
	moveThresholdPx: 0,
	hoverExpandMs: 0
};

function getTodoistCsrfToken() {
	const meta = document.querySelector('meta[name="csrf-token"]');
	return meta ? meta.getAttribute('content') : '';
}

function normalizeGoalParentId(rawValue) {
	if (rawValue === null || rawValue === undefined || rawValue === '') return null;
	const parsed = Number(rawValue);
	return Number.isFinite(parsed) ? parsed : null;
}

function initGoalDragAndDrop(root = document) {
	const goalsContainer = document.getElementById('goals-container');
	if (!goalsContainer) return;

	goalDragState.holdMs = Number(goalsContainer.dataset.dragHoldMs) || 0;
	goalDragState.moveThresholdPx = Number(goalsContainer.dataset.dragMoveThresholdPx) || 0;
	goalDragState.hoverExpandMs = Number(goalsContainer.dataset.dragHoverExpandMs) || 0;

	root.querySelectorAll('.goal-draggable:not([data-goal-dnd-ready])').forEach((item) => {
		item.dataset.goalDndReady = 'true';
		const button = item.querySelector('.goal-accordion-button');
		if (!button) return;
		button.addEventListener('mousedown', (event) => startGoalMousePress(event, item));
		button.addEventListener('touchstart', (event) => startGoalTouchPress(event, item), { passive: true });
		button.addEventListener('click', (event) => {
			if (goalDragState.suppressClick) {
				event.preventDefault();
				event.stopPropagation();
				goalDragState.suppressClick = false;
			}
		}, true);
	});
}

function startGoalPress(item, clientX, clientY) {
	goalDragState.item = item;
	goalDragState.startX = clientX;
	goalDragState.startY = clientY;
	goalDragState.active = false;
	goalDragState.target = null;
	goalDragState.targetParentId = undefined;

	clearTimeout(goalDragState.timer);
	goalDragState.timer = setTimeout(() => beginGoalDrag(clientX, clientY), goalDragState.holdMs);
}

function startGoalMousePress(event, item) {
	if (event.button !== 0) return;
	if (event.target.closest('.form-check, input, textarea, select, a')) return;

	startGoalPress(item, event.clientX, event.clientY);
	document.addEventListener('mousemove', handleGoalMouseMove);
	document.addEventListener('mouseup', endGoalMousePress, { once: true });
}

function startGoalTouchPress(event, item) {
	if (event.touches.length !== 1) return;
	if (event.target.closest('.form-check, input, textarea, select, a')) return;

	const touch = event.touches[0];
	startGoalPress(item, touch.clientX, touch.clientY);
	document.addEventListener('touchmove', handleGoalTouchMove, { passive: false });
	document.addEventListener('touchend', endGoalTouchPress, { once: true });
	document.addEventListener('touchcancel', cancelGoalDrag, { once: true });
}

function beginGoalDrag(clientX, clientY) {
	if (!goalDragState.item) return;

	goalDragState.active = true;
	goalDragState.suppressClick = true;
	goalDragState.item.classList.add('is-dragging');
	document.body.classList.add('goal-drag-active');

	const button = goalDragState.item.querySelector('.goal-accordion-button');
	goalDragState.ghost = button.cloneNode(true);
	goalDragState.ghost.className = `${button.className} goal-drag-ghost`;
	goalDragState.ghost.removeAttribute('data-bs-toggle');
	goalDragState.ghost.removeAttribute('data-bs-target');
	goalDragState.ghost.style.width = `${Math.min(button.getBoundingClientRect().width, window.innerWidth - 32)}px`;
	document.body.appendChild(goalDragState.ghost);
	updateGoalGhost(clientX, clientY);
	updateGoalDropTarget(clientX, clientY);
}

function handleGoalMove(clientX, clientY, event) {
	if (!goalDragState.item) return;

	const movedX = clientX - goalDragState.startX;
	const movedY = clientY - goalDragState.startY;
	const moved = Math.hypot(movedX, movedY);

	if (!goalDragState.active) {
		if (moved > goalDragState.moveThresholdPx) cancelGoalDrag();
		return;
	}

	event.preventDefault();
	updateGoalGhost(clientX, clientY);
	updateGoalDropTarget(clientX, clientY);
}

function handleGoalMouseMove(event) {
	handleGoalMove(event.clientX, event.clientY, event);
}

function handleGoalTouchMove(event) {
	if (event.touches.length !== 1) return;
	const touch = event.touches[0];
	handleGoalMove(touch.clientX, touch.clientY, event);
}

function updateGoalGhost(clientX, clientY) {
	if (!goalDragState.ghost) return;
	goalDragState.ghost.style.transform = `translate(${clientX + 12}px, ${clientY + 12}px)`;
}

function updateGoalDropTarget(clientX, clientY) {
	clearGoalDropTarget();

	const element = document.elementFromPoint(clientX, clientY);
	const targetItem = element ? element.closest('.goal-draggable') : null;
	if (!targetItem || targetItem === goalDragState.item || goalDragState.item.contains(targetItem)) {
		goalDragState.target = null;
		goalDragState.targetParentId = undefined;
		return;
	}

	const targetButton = targetItem.querySelector('.goal-accordion-button');
	if (targetButton) targetButton.classList.add('is-drop-target');
	goalDragState.target = targetItem;
	goalDragState.targetParentId = Number(targetItem.dataset.goalId);
	scheduleGoalHoverExpand(targetItem);
}

function scheduleGoalHoverExpand(targetItem) {
	clearTimeout(goalDragState.hoverTimer);
	const collapseEl = targetItem.querySelector(':scope > .goal-accordion-item > .accordion-collapse');
	if (!collapseEl || collapseEl.classList.contains('show')) return;

	goalDragState.hoverTimer = setTimeout(() => {
		if (goalDragState.active && goalDragState.target === targetItem && window.bootstrap) {
			bootstrap.Collapse.getOrCreateInstance(collapseEl, { toggle: false }).show();
		}
	}, goalDragState.hoverExpandMs);
}

function clearGoalDropTarget() {
	clearTimeout(goalDragState.hoverTimer);
	document.querySelectorAll('.is-drop-target').forEach((target) => {
		target.classList.remove('is-drop-target');
	});
}

function endGoalPress(clientX, clientY, event) {
	clearTimeout(goalDragState.timer);
	document.removeEventListener('mousemove', handleGoalMouseMove);
	document.removeEventListener('touchmove', handleGoalTouchMove);
	document.removeEventListener('touchcancel', cancelGoalDrag);

	if (!goalDragState.active || !goalDragState.item) {
		resetGoalDragState();
		return;
	}

	event.preventDefault();
	if (clientX !== null && clientY !== null) updateGoalDropTarget(clientX, clientY);
	const goalId = Number(goalDragState.item.dataset.goalId);
	const currentParentId = normalizeGoalParentId(goalDragState.item.dataset.parentId);
	const nextParentId = goalDragState.targetParentId;

	resetGoalDragState();
	if (nextParentId === undefined || currentParentId === nextParentId) return;
	reparentGoal(goalId, nextParentId);
}

function endGoalMousePress(event) {
	endGoalPress(event.clientX, event.clientY, event);
}

function endGoalTouchPress(event) {
	const touch = event.changedTouches[0];
	endGoalPress(touch ? touch.clientX : null, touch ? touch.clientY : null, event);
}

function cancelGoalDrag() {
	clearTimeout(goalDragState.timer);
	document.removeEventListener('mousemove', handleGoalMouseMove);
	document.removeEventListener('mouseup', endGoalMousePress);
	document.removeEventListener('touchmove', handleGoalTouchMove);
	document.removeEventListener('touchend', endGoalTouchPress);
	document.removeEventListener('touchcancel', cancelGoalDrag);
	resetGoalDragState();
	goalDragState.suppressClick = false;
}

function resetGoalDragState() {
	clearGoalDropTarget();
	if (goalDragState.item) goalDragState.item.classList.remove('is-dragging');
	if (goalDragState.ghost) goalDragState.ghost.remove();
	document.body.classList.remove('goal-drag-active');

	goalDragState.item = null;
	goalDragState.active = false;
	goalDragState.ghost = null;
	goalDragState.target = null;
	goalDragState.targetParentId = undefined;
}

function reparentGoal(goalId, parentId) {
	fetch('/todoist/goal/reparent', {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			'X-CSRFToken': getTodoistCsrfToken()
		},
		body: JSON.stringify({ goal_id: goalId, parent_id: parentId })
	})
	.then(response => response.json().then(data => ({ ok: response.ok, data })))
	.then(({ ok, data }) => {
		if (ok && data.success) {
			return refreshTodoistPage(window.location.href);
		}
		throw new Error(data.error || 'Failed to move goal');
	})
	.catch(error => {
		console.error('Failed to move goal:', error);
		alert(error.message || 'Failed to move goal');
	});
}

function unparentGoal(goalId) {
	reparentGoal(goalId, null);
}

async function refreshTodoistPage(url, options = {}) {
	const expandedIds = [...document.querySelectorAll('#app-main-content .accordion-collapse.show')].map(el => el.id);
	const response = await fetch(url, options);
	if (!response.ok) throw new Error('The goal update could not be saved.');

	const nextDocument = new DOMParser().parseFromString(await response.text(), 'text/html');
	const nextMain = nextDocument.getElementById('app-main-content');
	const nextModals = nextDocument.getElementById('app-actions-modals');
	const nextFlash = nextDocument.getElementById('app-flash-messages');
	if (!nextMain || !nextModals || !nextFlash) throw new Error('The updated goals could not be displayed.');

	document.getElementById('app-main-content').replaceWith(nextMain);
	document.getElementById('app-actions-modals').replaceWith(nextModals);
	document.getElementById('app-flash-messages').replaceWith(nextFlash);
	document.querySelectorAll('.modal-backdrop').forEach(el => el.remove());
	document.body.classList.remove('modal-open');
	document.body.style.removeProperty('padding-right');
	document.body.style.removeProperty('overflow');
	expandedIds.forEach(id => {
		const collapse = document.getElementById(id);
		if (collapse) bootstrap.Collapse.getOrCreateInstance(collapse, { toggle: false }).show();
	});
	initGoalDragAndDrop();
}

document.addEventListener('submit', async event => {
	const form = event.target.closest('form[data-async-mutation]');
	if (!form) return;
	event.preventDefault();
	const submitButton = event.submitter;
	if (submitButton) submitButton.disabled = true;
	try {
		await refreshTodoistPage(form.action, { method: form.method, body: new FormData(form) });
	} catch (error) {
		if (submitButton) submitButton.disabled = false;
		alert(error.message);
	}
});

document.addEventListener('click', async event => {
	const link = event.target.closest('a[data-async-mutation]');
	if (!link) return;
	event.preventDefault();
	try {
		await refreshTodoistPage(link.href);
	} catch (error) {
		alert(error.message);
	}
});

// Pagination for summary goals
let summaryCurrentPage = 0;
let summaryHasMoreGoals = false;

function loadMoreSummaryGoals() {
	const btn = document.getElementById('load-more-btn');
	if (!btn) return;
	
	btn.disabled = true;
	btn.innerHTML = '<i class="bi bi-hourglass-split me-2"></i>Loading...';
	
	summaryCurrentPage++;
	fetch(`/todoist/api/summary_goals_page?page=${summaryCurrentPage}`)
		.then(response => response.json())
		.then(data => {
			const container = document.getElementById('goals-container');
			container.innerHTML += data.html;
			initGoalDragAndDrop(container);
			summaryHasMoreGoals = data.has_more;
			
			if (!summaryHasMoreGoals) {
				btn.remove();
			} else {
				btn.disabled = false;
				btn.innerHTML = '<i class="bi bi-arrow-down me-2"></i>Load More';
			}
		})
		.catch(error => {
			console.error('Error loading more goals:', error);
			btn.disabled = false;
			btn.innerHTML = '<i class="bi bi-arrow-down me-2"></i>Load More';
		});
}

// Pagination for completed goals
let completedCurrentPage = 0;
let completedHasMoreGoals = false;

function loadMoreCompletedGoals() {
	const btn = document.getElementById('load-more-btn');
	if (!btn) return;
	
	btn.disabled = true;
	btn.innerHTML = '<i class="bi bi-hourglass-split me-2"></i>Loading...';
	
	completedCurrentPage++;
	fetch(`/todoist/api/completed_goals_page?page=${completedCurrentPage}`)
		.then(response => response.json())
		.then(data => {
			const container = document.getElementById('goals-container');
			container.innerHTML += data.html;
			completedHasMoreGoals = data.has_more;
			
			if (!completedHasMoreGoals) {
				btn.remove();
			} else {
				btn.disabled = false;
				btn.innerHTML = '<i class="bi bi-arrow-down me-2"></i>Load More';
			}
		})
		.catch(error => {
			console.error('Error loading more goals:', error);
			btn.disabled = false;
			btn.innerHTML = '<i class="bi bi-arrow-down me-2"></i>Load More';
		});
}

// Initialize pagination based on which page we're on
document.addEventListener('DOMContentLoaded', function() {
	document.addEventListener('change', function(event) {
		const checkbox = event.target.closest('[data-goal-state-id]');
		if (checkbox) toggleGoalState(checkbox, Number(checkbox.dataset.goalStateId));
	});
	document.addEventListener('click', function(event) {
		const button = event.target.closest('[data-unparent-goal-id]');
		if (button) unparentGoal(Number(button.dataset.unparentGoalId));
	});
	document.querySelectorAll('button[data-submit-once]').forEach(function(button) {
		button.form?.addEventListener('submit', function() {
			button.disabled = true;
			button.innerHTML = '<i class="bi bi-hourglass-split me-1"></i>Creating...';
		});
	});

	const loadMoreButton = document.querySelector('[data-load-more]');
	if (loadMoreButton?.dataset.loadMore === 'summary') {
		loadMoreButton.addEventListener('click', loadMoreSummaryGoals);
	} else if (loadMoreButton?.dataset.loadMore === 'completed') {
		loadMoreButton.addEventListener('click', loadMoreCompletedGoals);
	}

	initGoalDragAndDrop();
});
