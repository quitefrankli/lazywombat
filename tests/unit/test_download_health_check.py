from unittest.mock import MagicMock, patch


class TestSendAlertEmail:
    @patch('web_app.scheduled_jobs.smtplib.SMTP')
    @patch('web_app.scheduled_jobs.ConfigManager')
    def test_sends_email_with_correct_mime(self, mock_config, mock_smtp_class):
        mock_config.return_value.smtp_host = 'smtp.test.com'
        mock_config.return_value.smtp_port = 587
        mock_config.return_value.smtp_user = 'sender@test.com'
        mock_config.return_value.smtp_password = 'secret'
        mock_config.return_value.alert_email_to = 'alert@test.com'

        mock_smtp = MagicMock()
        mock_smtp_class.return_value.__enter__ = MagicMock(return_value=mock_smtp)
        mock_smtp_class.return_value.__exit__ = MagicMock(return_value=False)

        from web_app.scheduled_jobs import send_alert_email
        send_alert_email('Test Subject', 'Test body')

        mock_smtp.starttls.assert_called_once()
        mock_smtp.login.assert_called_once_with('sender@test.com', 'secret')
        mock_smtp.sendmail.assert_called_once()

        args = mock_smtp.sendmail.call_args[0]
        assert args[0] == 'sender@test.com'
        assert args[1] == 'alert@test.com'
        assert 'Test Subject' in args[2]
        assert 'Test body' in args[2]
