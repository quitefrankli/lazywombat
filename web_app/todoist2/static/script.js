function toggleGoalState(switchElement, goalId) {
	const state = switchElement.checked;

	const data = {
		"goal_id": goalId,
		"state": state
	};

	fetch("/todoist2/goal/toggle_state", {
		method: "POST",
		headers: {
			"Content-Type": "application/json"
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

// Pagination for summary goals
let summaryCurrentPage = 0;
let summaryHasMoreGoals = false;

function loadMoreSummaryGoals() {
	const btn = document.getElementById('load-more-btn');
	if (!btn) return;
	
	btn.disabled = true;
	btn.innerHTML = '<i class="bi bi-hourglass-split me-2"></i>Loading...';
	
	summaryCurrentPage++;
	fetch(`/todoist2/api/summary_goals_page?page=${summaryCurrentPage}`)
		.then(response => response.json())
		.then(data => {
			const container = document.getElementById('goals-container');
			container.innerHTML += data.html;
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
	fetch(`/todoist2/api/completed_goals_page?page=${completedCurrentPage}`)
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
	const summaryGoalsPage = document.querySelector('body').classList.contains('summary-goals-page');
	const completedGoalsPage = document.querySelector('body').classList.contains('completed-goals-page');
	
	if (summaryGoalsPage) {
		const btn = document.getElementById('load-more-btn');
		if (btn) {
			btn.onclick = loadMoreSummaryGoals;
		}
	} else if (completedGoalsPage) {
		const btn = document.getElementById('load-more-btn');
		if (btn) {
			btn.onclick = loadMoreCompletedGoals;
		}
	}
});