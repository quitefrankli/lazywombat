"""Unit tests for todoist module"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, timedelta, date

from web_app.app import app
from web_app.users import User
from web_app.todoist import (
    todoist_api,
    _get_filtered_summary_goals,
    _goals_to_blocks,
    _get_completed_goals,
    _completed_goals_to_blocks,
    PAGE_SIZE
)
from web_app.todoist.data_interface import Goal, GoalState, Goals


@pytest.fixture
def client():
    """Create a test client for the Flask app"""
    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = False
    with app.test_client() as client:
        yield client


@pytest.fixture
def test_user():
    """Create a test user"""
    return User(username='testuser', password='testpass', folder='test_folder', is_admin=False)


@pytest.fixture
def test_goal():
    """Create a test goal"""
    return Goal(
        id=1,
        name='Complete project',
        state=GoalState.ACTIVE,
        description='Finish the project',
        creation_date=datetime(2026, 1, 1),
        last_modified=datetime(2026, 2, 1)
    )


@pytest.fixture
def test_goals(test_goal):
    """Create multiple test goals"""
    goals = [
        test_goal,
        Goal(
            id=2,
            name='Exercise',
            state=GoalState.COMPLETED,
            description='Daily exercise',
            creation_date=datetime(2026, 1, 15),
            last_modified=datetime(2026, 2, 2),
            completion_date=datetime(2026, 2, 2)
        ),
        Goal(
            id=3,
            name='Read book',
            state=GoalState.BACKLOGGED,
            description='Read the book',
            creation_date=datetime(2026, 1, 20),
            last_modified=datetime(2026, 2, 3)
        ),
    ]
    return Goals(goals={goal.id: goal for goal in goals})


class TestTodoistHelperFunctions:
    """Tests for todoist helper functions"""

    @patch('web_app.todoist.DataInterface')
    def test_child_goals_excluded_from_summary(self, mock_di, test_user):
        """Child goals with a parent should not appear in the top-level summary"""
        parent = Goal(id=1, name='Parent', state=GoalState.ACTIVE,
                      last_modified=datetime(2026, 2, 1), children=[2])
        child = Goal(id=2, name='Child', state=GoalState.ACTIVE,
                     last_modified=datetime(2026, 2, 1), parent=1)
        mock_instance = Mock()
        mock_instance.load_goals.return_value = Goals(goals={1: parent, 2: child})
        mock_di.return_value = mock_instance

        goals, _ = _get_filtered_summary_goals(test_user)

        assert len(goals) == 1
        assert goals[0].id == 1

    @patch('web_app.todoist.DataInterface')
    def test_summary_sorted_by_most_recent_subgoal(self, mock_di, test_user):
        """Parent goal with a recently modified subgoal should sort above an older parent"""
        older_parent = Goal(id=1, name='Older', state=GoalState.ACTIVE,
                            last_modified=datetime(2026, 1, 1), children=[3])
        newer_parent = Goal(id=2, name='Newer standalone', state=GoalState.ACTIVE,
                            last_modified=datetime(2026, 1, 10))
        recent_child = Goal(id=3, name='Recent child', state=GoalState.ACTIVE,
                            last_modified=datetime(2026, 1, 20), parent=1)
        mock_instance = Mock()
        mock_instance.load_goals.return_value = Goals(goals={1: older_parent, 2: newer_parent, 3: recent_child})
        mock_di.return_value = mock_instance

        goals, _ = _get_filtered_summary_goals(test_user)

        assert goals[0].id == 1  # older_parent sorts first because its child is most recent

    def test_goals_to_blocks_empty(self):
        """Test goals_to_blocks with empty list"""
        blocks = _goals_to_blocks([])
        assert blocks == []

    def test_goals_to_blocks_single_goal(self, test_goal):
        """Test goals_to_blocks with single goal"""
        blocks = _goals_to_blocks([test_goal])
        assert len(blocks) == 1
        assert len(blocks[0][1]) == 1

    def test_goals_to_blocks_multiple_same_date(self, test_goal):
        """Test goals_to_blocks with multiple goals on same date"""
        goal1 = test_goal
        goal2 = Goal(
            id=2,
            name='Another goal',
            state=GoalState.ACTIVE,
            last_modified=goal1.last_modified
        )
        
        blocks = _goals_to_blocks([goal1, goal2])
        assert len(blocks) == 1
        assert len(blocks[0][1]) == 2

    def test_goals_to_blocks_multiple_dates(self, test_goal):
        """Test goals_to_blocks with goals on different dates"""
        goal1 = test_goal
        goal2 = Goal(
            id=2,
            name='Another goal',
            state=GoalState.ACTIVE,
            last_modified=goal1.last_modified + timedelta(days=1)
        )
        
        blocks = _goals_to_blocks([goal1, goal2])
        assert len(blocks) == 2

    @patch('web_app.todoist.DataInterface')
    def test_get_completed_goals(self, mock_di, test_user, test_goals):
        """Test getting completed goals"""
        mock_instance = Mock()
        mock_instance.load_goals.return_value = test_goals
        mock_di.return_value = mock_instance

        goals = _get_completed_goals(test_user)
        
        # Should only return completed goals
        assert all(goal.state == GoalState.COMPLETED for goal in goals)

    def test_completed_goals_to_blocks_empty(self):
        """Test completed_goals_to_blocks with empty list"""
        blocks = _completed_goals_to_blocks([])
        assert blocks == []

    def test_completed_goals_to_blocks_single_goal(self):
        """Test completed_goals_to_blocks with single goal"""
        goal = Goal(
            id=1,
            name='Completed',
            state=GoalState.COMPLETED,
            completion_date=datetime(2026, 2, 1),
            last_modified=datetime.now()
        )
        blocks = _completed_goals_to_blocks([goal])
        assert len(blocks) == 1
        assert len(blocks[0][1]) == 1

    def test_completed_goals_to_blocks_multiple_dates(self):
        """Test completed_goals_to_blocks with multiple dates"""
        goal1 = Goal(
            id=1,
            name='Goal 1',
            state=GoalState.COMPLETED,
            completion_date=datetime(2026, 2, 1),
            last_modified=datetime.now()
        )
        goal2 = Goal(
            id=2,
            name='Goal 2',
            state=GoalState.COMPLETED,
            completion_date=datetime(2026, 2, 2),
            last_modified=datetime.now()
        )
        
        blocks = _completed_goals_to_blocks([goal1, goal2])
        assert len(blocks) == 2


def test_goal_mutations_use_partial_page_updates():
    summary_template = open('web_app/todoist/templates/summary_goals.html').read()
    page_template = open('web_app/todoist/templates/summary_goals_page.html').read()
    script = open('web_app/todoist/static/script.js').read()

    assert 'data-async-mutation' in summary_template
    assert 'data-async-mutation' in page_template
    assert 'refreshTodoistPage' in script
    assert 'window.location.reload' not in script


class TestGoalReparenting:
    """Tests for changing goal hierarchy"""

    def test_reparent_goal_between_parent_and_root(self):
        from web_app.todoist.goals import reparent_goal_in_tree

        parent = Goal(id=1, name='Parent', state=GoalState.ACTIVE,
                      last_modified=datetime(2026, 1, 1), children=[2])
        child = Goal(id=2, name='Child', state=GoalState.ACTIVE,
                     last_modified=datetime(2026, 1, 1), parent=1)
        new_parent = Goal(id=3, name='New parent', state=GoalState.ACTIVE,
                          last_modified=datetime(2026, 1, 1))
        tld = Goals(goals={1: parent, 2: child, 3: new_parent})

        reparent_goal_in_tree(tld, 2, 3)

        assert parent.children == []
        assert child.parent == 3
        assert new_parent.children == [2]

        reparent_goal_in_tree(tld, 2, None)

        assert child.parent is None
        assert new_parent.children == []

    def test_reparent_goal_rejects_self_and_descendant_parent(self):
        from web_app.todoist.goals import reparent_goal_in_tree

        parent = Goal(id=1, name='Parent', state=GoalState.ACTIVE,
                      last_modified=datetime(2026, 1, 1), children=[2])
        child = Goal(id=2, name='Child', state=GoalState.ACTIVE,
                     last_modified=datetime(2026, 1, 1), parent=1, children=[3])
        grandchild = Goal(id=3, name='Grandchild', state=GoalState.ACTIVE,
                          last_modified=datetime(2026, 1, 1), parent=2)
        tld = Goals(goals={1: parent, 2: child, 3: grandchild})

        with pytest.raises(ValueError):
            reparent_goal_in_tree(tld, 1, 1)

        with pytest.raises(ValueError):
            reparent_goal_in_tree(tld, 1, 3)

        assert parent.parent is None
        assert parent.children == [2]


def test_goal_creation_dates_are_independent_per_instance():
    """Regression guard: the datetime default must be evaluated per-instance
    (default_factory), not once at import — otherwise every Goal would share the
    process-start timestamp."""
    import time

    g1 = Goal(id=1, name='a', state=GoalState.ACTIVE)
    time.sleep(0.01)
    g2 = Goal(id=2, name='b', state=GoalState.ACTIVE)
    assert g1.creation_date != g2.creation_date


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
