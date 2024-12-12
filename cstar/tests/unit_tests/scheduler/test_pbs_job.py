import json
import pytest
from unittest.mock import MagicMock, patch, PropertyMock
from cstar.system.scheduler import PBSScheduler, PBSQueue
from cstar.scheduler.job import (
    JobStatus,
    PBSJob,
)


class TestPBSJob:
    """Tests for the PBSJob class."""

    def setup_method(self, method):
        # Create PBSQueue with specified max_walltime
        self.mock_queue = PBSQueue(name="test_queue", max_walltime="02:00:00")

        # Create the PBSScheduler with the mock queue
        self.scheduler = PBSScheduler(
            queues=[self.mock_queue],
            primary_queue_name="test_queue",
        )

        # Define common job parameters
        self.common_job_params = {
            "scheduler": self.scheduler,
            "commands": "echo Hello, World",
            "account_key": "test_account",
            "cpus": 4,
            "nodes": 2,
            "walltime": "02:00:00",
            "job_name": "test_pbs_job",
            "output_file": "/test/pbs_output.log",
            "queue_name": "test_queue",
        }

    @patch(
        "cstar.system.manager.CStarSystemManager.scheduler", new_callable=PropertyMock
    )
    def test_script(self, mock_scheduler):
        # Mock the scheduler and its attributes
        mock_scheduler.return_value = MagicMock()
        mock_scheduler.return_value.other_scheduler_directives = {
            "mock_directive": "mock_value"
        }

        # Initialize a PBSJob
        job = PBSJob(**self.common_job_params)

        expected_script = (
            "#PBS -S /bin/bash\n"
            "#PBS -N test_pbs_job\n"
            "#PBS -o /test/pbs_output.log\n"
            "#PBS -A test_account\n"
            "#PBS -l select=2:ncpus=2,walltime=02:00:00\n"
            "#PBS -q test_queue\n"
            "#PBS -j oe\n"
            "#PBS -k eod\n"
            "#PBS -V\n"
            "#PBS mock_directive mock_value"
            "\ncd ${PBS_O_WORKDIR}"
            "\n\necho Hello, World"
        )

        # Validate the script content
        assert (
            job.script.strip() == expected_script.strip()
        ), f"Script mismatch!\nExpected:\n{expected_script}\n\nGot:\n{job.script}"

    @patch("subprocess.run")
    @patch(
        "cstar.system.manager.CStarSystemManager.scheduler", new_callable=PropertyMock
    )
    def test_submit(self, mock_scheduler, mock_subprocess, tmp_path):
        # Mock scheduler attributes
        mock_scheduler.return_value = MagicMock()
        mock_scheduler.return_value.other_scheduler_directives = {}

        # Mock subprocess.run for qsub
        mock_subprocess.return_value = MagicMock(
            returncode=0, stdout="12345.mockserver\n", stderr=""
        )

        # Create temporary paths
        script_path = tmp_path / "test_pbs_job.sh"
        run_path = tmp_path

        # Initialize a PBSJob
        job = PBSJob(
            **self.common_job_params,
            script_path=script_path,
            run_path=run_path,
        )

        # Submit the job
        job.submit()

    @pytest.mark.parametrize(
        "returncode, stdout, stderr, match_message",
        [
            (1, "", "Error submitting job", "Non-zero exit code when submitting job"),
            (0, "InvalidJobIDFormat", "", "Unexpected job ID format from qsub"),
        ],
    )
    @patch("subprocess.run")
    @patch(
        "cstar.system.manager.CStarSystemManager.scheduler", new_callable=PropertyMock
    )
    def test_submit_raises(
        self,
        mock_scheduler,
        mock_subprocess,
        returncode,
        stdout,
        stderr,
        match_message,
        tmp_path,
    ):
        # Mock scheduler attributes
        mock_scheduler.return_value = MagicMock()
        mock_scheduler.return_value.other_scheduler_directives = {}

        # Mock subprocess.run for qsub
        mock_subprocess.return_value = MagicMock(
            returncode=returncode, stdout=stdout, stderr=stderr
        )

        # Create temporary paths
        script_path = tmp_path / "test_pbs_job.sh"
        run_path = tmp_path

        # Initialize a PBSJob
        job = PBSJob(
            **self.common_job_params,
            script_path=script_path,
            run_path=run_path,
        )

        # Check for expected exceptions
        with pytest.raises(RuntimeError, match=match_message):
            job.submit()

    @patch("subprocess.run")
    @patch("cstar.scheduler.job.PBSJob.status", new_callable=PropertyMock)
    def test_cancel_running_job(self, mock_status, mock_subprocess, tmp_path):
        # Mock the status to "running"
        mock_status.return_value = JobStatus.RUNNING

        # Mock subprocess.run for successful cancellation
        mock_subprocess.return_value = MagicMock(returncode=0, stdout="", stderr="")

        # Create a PBSJob with a set job ID
        job = PBSJob(
            **self.common_job_params,
            run_path=tmp_path,
        )
        job._id = 12345  # Manually set the job ID

        # Cancel the job
        job.cancel()

        # Verify qdel was called correctly
        mock_subprocess.assert_called_once_with(
            "qdel 12345",
            shell=True,
            cwd=tmp_path,
            capture_output=True,
            text=True,
        )

    @patch("subprocess.run")
    @patch("cstar.scheduler.job.PBSJob.status", new_callable=PropertyMock)
    @patch("builtins.print")
    def test_cancel_completed_job(
        self, mock_print, mock_status, mock_subprocess, tmp_path
    ):
        # Mock the status to "completed"
        mock_status.return_value = "completed"

        # Create a PBSJob with a set job ID
        job = PBSJob(
            **self.common_job_params,
            run_path=tmp_path,
        )
        job._id = 12345  # Manually set the job ID

        # Attempt to cancel the job
        job.cancel()

        # Verify the message was printed
        mock_print.assert_called_with("Cannot cancel job with status completed")

        # Verify qdel was not called
        mock_subprocess.assert_not_called()

    ##
    @patch("subprocess.run")
    @patch("cstar.scheduler.job.PBSJob.status", new_callable=PropertyMock)
    def test_cancel_failure(self, mock_status, mock_subprocess, tmp_path):
        # Mock the status to "running"
        mock_status.return_value = JobStatus.RUNNING

        # Mock subprocess.run for qdel failure
        mock_subprocess.return_value = MagicMock(
            returncode=1, stdout="", stderr="Error cancelling job"
        )

        # Create a PBSJob with a set job ID
        job = PBSJob(
            **self.common_job_params,
            run_path=tmp_path,
        )
        job._id = 12345  # Manually set the job ID

        # Expect an error when qdel fails
        with pytest.raises(
            RuntimeError, match="Non-zero exit code when cancelling job"
        ):
            job.cancel()

    ##
    @pytest.mark.parametrize(
        "qstat_output, exit_status, expected_status, should_raise, expected_exception, expected_message",
        [
            (
                {"Jobs": {"12345": {"job_state": "Q"}}},
                None,
                JobStatus.PENDING,
                False,
                None,
                None,
            ),  # Pending job
            (
                {"Jobs": {"12345": {"job_state": "R"}}},
                None,
                JobStatus.RUNNING,
                False,
                None,
                None,
            ),  # Running job
            (
                {"Jobs": {"12345": {"job_state": "C"}}},
                None,
                JobStatus.COMPLETED,
                False,
                None,
                None,
            ),  # Completed job
            (
                {"Jobs": {"12345": {"job_state": "H"}}},
                None,
                JobStatus.HELD,
                False,
                None,
                None,
            ),  # Held job
            (
                {"Jobs": {"12345": {"job_state": "F", "Exit_status": 1}}},
                None,
                JobStatus.FAILED,
                False,
                None,
                None,
            ),  # Failed job
            (
                {"Jobs": {"12345": {"job_state": "F", "Exit_status": 0}}},
                None,
                JobStatus.COMPLETED,
                False,
                None,
                None,
            ),  # Completed with Exit_status 0
            (
                None,
                1,
                None,
                True,
                RuntimeError,
                "Failed to retrieve job status using qstat",
            ),  # qstat command failure
            (
                {"Jobs": {}},
                None,
                None,
                True,
                RuntimeError,
                "Job ID 12345 not found in qstat output.",
            ),  # Missing job info
            (
                {"Jobs": {"12345": {"job_state": "E"}}},
                None,
                JobStatus.ENDING,
                False,
                None,
                None,
            ),  # Ending job
            (
                {"Jobs": {"12345": {"job_state": "X"}}},
                None,
                JobStatus.UNKNOWN,
                False,
                None,
                None,
            ),  # Unknown job state
            (
                "invalid_json",
                None,
                None,
                True,
                RuntimeError,
                "Failed to parse JSON from qstat output",
            ),  # JSONDecodeError
        ],
    )
    @patch("subprocess.run")
    def test_status(
        self,
        mock_subprocess,
        qstat_output,
        exit_status,
        expected_status,
        should_raise,
        expected_exception,
        expected_message,
    ):
        # Mock qstat command output
        if qstat_output is not None:
            if qstat_output == "invalid_json":
                mock_subprocess.return_value = MagicMock(
                    returncode=0, stdout="Invalid JSON", stderr=""
                )
            else:
                mock_subprocess.return_value = MagicMock(
                    returncode=0, stdout=json.dumps(qstat_output), stderr=""
                )
        else:
            mock_subprocess.return_value = MagicMock(
                returncode=1, stdout="", stderr="Error: qstat command failed"
            )

        # Create a PBSJob with a set job ID
        job = PBSJob(**self.common_job_params)
        job._id = 12345  # Manually set the job ID

        # Check the expected outcome
        if should_raise:
            with pytest.raises(expected_exception, match=expected_message):
                job.status
        else:
            assert (
                job.status == expected_status
            ), f"Expected status '{expected_status}' but got '{job.status}'"

    @patch("json.loads", side_effect=json.JSONDecodeError("Expecting value", "", 0))
    @patch("subprocess.run")
    def test_status_json_decode_error(self, mock_subprocess, mock_json_loads):
        # Mock qstat command output
        mock_subprocess.return_value = MagicMock(
            returncode=0, stdout="Invalid JSON", stderr=""
        )

        # Create a PBSJob with a set job ID
        job = PBSJob(**self.common_job_params)
        job._id = 12345  # Manually set the job ID

        # Check for JSONDecodeError handling
        with pytest.raises(
            RuntimeError, match="Failed to parse JSON from qstat output"
        ):
            job.status