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