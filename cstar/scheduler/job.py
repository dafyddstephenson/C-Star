## STIL TODO:
# Write docstrings and rtd pages
import os
import re
import time
import json
import warnings
import subprocess
from math import ceil
from enum import Enum, auto
from abc import ABC, abstractmethod
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Tuple
from cstar.system.manager import cstar_sysmgr
from cstar.system.scheduler import (
    SlurmScheduler,
    PBSScheduler,
    Scheduler,
    SlurmQOS,
    SlurmPartition,
)


def create_scheduler_job(
    commands: str,
    account_key: str,
    cpus: int,
    nodes: Optional[int] = None,
    cpus_per_node: Optional[int] = None,
    script_path: Optional[str | Path] = None,
    run_path: Optional[str | Path] = None,
    job_name: Optional[str] = None,
    output_file: Optional[str | Path] = None,
    queue_name: Optional[str] = None,
    send_email: Optional[bool] = True,
    walltime: Optional[str] = None,
) -> "SchedulerJob":
    # mypy assigns type based on first condition, assigning explicitly:
    job_type: type[SlurmJob] | type[PBSJob]

    if isinstance(cstar_sysmgr.scheduler, SlurmScheduler):
        job_type = SlurmJob
    elif isinstance(cstar_sysmgr.scheduler, PBSScheduler):
        job_type = PBSJob
    else:
        raise TypeError(
            f"Unsupported scheduler type: {type(cstar_sysmgr.scheduler).__name__}"
        )

    return job_type(
        scheduler=cstar_sysmgr.scheduler,
        commands=commands,
        cpus=cpus,
        nodes=nodes,
        cpus_per_node=cpus_per_node,
        account_key=account_key,
        script_path=script_path,
        run_path=run_path,
        job_name=job_name,
        output_file=output_file,
        queue_name=queue_name,
        send_email=send_email,
        walltime=walltime,
    )


class JobStatus(Enum):
    UNSUBMITTED = auto()
    PENDING = auto()
    RUNNING = auto()
    COMPLETED = auto()
    CANCELLED = auto()
    FAILED = auto()
    HELD = auto()
    ENDING = auto()
    UNKNOWN = auto()

    def __str__(self):
        return self.name.lower()  # Convert enum name to lowercase for display


class SchedulerJob(ABC):
    def __init__(
        self,
        scheduler: "Scheduler",
        commands: str,
        account_key: str,
        cpus: int,
        nodes: Optional[int] = None,
        cpus_per_node: Optional[int] = None,
        script_path: Optional[str | Path] = None,
        run_path: Optional[str | Path] = None,
        job_name: Optional[str] = None,
        output_file: Optional[str | Path] = None,
        queue_name: Optional[str] = None,
        send_email: Optional[bool] = True,
        walltime: Optional[str] = None,
    ):
        self.scheduler = scheduler
        self.commands = commands
        self.cpus = cpus

        default_name = f"cstar_job_{datetime.strftime(datetime.now(), '%Y%m%d_%H%M%S')}"

        self.script_path = (
            Path.cwd() / f"{default_name}.sh"
            if script_path is None
            else Path(script_path)
        )
        self.run_path = self.script_path.parent if run_path is None else Path(run_path)
        self.job_name = default_name if job_name is None else job_name
        self.output_file = (
            self.run_path / f"{default_name}.out"
            if output_file is None
            else output_file
        )
        self.queue_name = (
            scheduler.primary_queue_name if queue_name is None else queue_name
        )
        self.queue = scheduler.get_queue(queue_name)
        self.walltime = walltime

        if (walltime is None) and (self.queue.max_walltime is None):
            raise ValueError(
                "Cannot create scheduler job: walltime parameter not provided "
                + f"and C-Star cannot default to the max walltime for the queue {queue_name} "
                + " as it cannot be determined"
            )
        elif self.queue.max_walltime is None:
            warnings.warn(
                f"WARNING: Unable to determine the maximum allowed walltime for chosen queue {queue_name}. "
                + f"If your chosen walltime {walltime} exceeds the (unknown) limit, this job may be "
                + "rejected by your system's job scheduler.",
                UserWarning,
            )
        elif walltime is None:
            warnings.warn(
                "Walltime parameter unspecified. Creating scheduler job with maximum walltime "
                + f"for queue {queue_name}, {self.queue.max_walltime}"
            )
            self.walltime = self.queue.max_walltime
        else:
            # Check walltimes
            wt_h, wt_m, wt_s = map(int, walltime.split(":"))
            mw_h, mw_m, mw_s = map(int, self.queue.max_walltime.split(":"))

            walltime_delta = timedelta(hours=wt_h, minutes=wt_m, seconds=wt_s)
            max_walltime_delta = timedelta(hours=mw_h, minutes=mw_m, seconds=mw_s)

            if walltime_delta > max_walltime_delta:
                raise ValueError(
                    f"Selected walltime {walltime} exceeds maximum "
                    + f"walltime for selected queue {queue_name}: "
                    + f"{self.queue.max_walltime}"
                )

        self.cpus = cpus

        # Explicitly typing to avoid mypy confusion in conditional pathways below
        self.cpus_per_node: Optional[int]
        self.nodes: Optional[int]

        if (
            (nodes is None)
            and (cpus_per_node is not None)
            and (scheduler.requires_task_distribution)
        ):
            self.nodes = ceil(cpus / cpus_per_node)
            self.cpus_per_node = cpus_per_node
        elif (
            (nodes is not None)
            and (cpus_per_node is None)
            and (scheduler.requires_task_distribution)
        ):
            self.nodes = nodes
            self.cpus_per_node = int(cpus / nodes)
        elif (
            (nodes is None)
            and (cpus_per_node is None)
            and (scheduler.requires_task_distribution)
        ):
            if scheduler.global_max_cpus_per_node is None:
                raise EnvironmentError(
                    "You attempted to create a scheduler job without 'nodes', and "
                    + "'cpus_per_node' parameters, but your scheduler explicitly "
                    + "requires a task distribution. C-Star is unable to determine "
                    + "your system's CPUs per node automatically and cannot continue"
                )

            nnodes, ncpus = self._calculate_node_distribution(
                cpus, scheduler.global_max_cpus_per_node
            )
            warnings.warn(
                (
                    "WARNING: Attempting to create scheduler job without 'nodes' and 'cpus_per_node' "
                    + "parameters, but your system requires an explicitly specified task distribution."
                    + "\n C-Star will attempt "
                    + f"\nto use a distribution of {nnodes} nodes with {ncpus} CPUs each, "
                    + "\nbased on your system maximum of "
                    + f"{scheduler.global_max_cpus_per_node} CPUS per node "
                    + f"\nand your job requirement of {cpus} CPUS."
                ),
                UserWarning,
            )
            self.cpus_per_node = ncpus
            self.nodes = nnodes
        else:
            self.cpus_per_node = cpus_per_node
            self.nodes = nodes

        self.account_key = account_key
        self._id = None

    @property
    def id(self):
        if self._id is None:
            print("No Job ID found. Submit this job with SchedulerJob.submit()")
        return self._id

    def save_script(self):
        with open(self.script_path, "w") as f:
            f.write(self.script)

    @abstractmethod
    def submit(self):
        pass

    @abstractmethod
    def status(self):
        pass

    def updates(self, seconds=10):
        """Provides updates from the job's output file as a live stream for `seconds`
        seconds (default 10).

        If `seconds` is 0, updates are provided indefinitely until the user interrupts the stream.
        """

        if self.status != JobStatus.RUNNING:
            print(
                f"This job is currently not running ({self.status}). Live updates cannot be provided."
            )
            if (self.status in {JobStatus.FAILED, JobStatus.COMPLETED}) or (
                self.status == JobStatus.CANCELLED and self.output_file.exists()
            ):
                print(f"See {self.output_file.resolve()} for job output")
            return

        if seconds == 0:
            # Confirm indefinite tailing
            confirmation = (
                input(
                    "This will provide indefinite updates to your job. You can stop it anytime using Ctrl+C. "
                    "Do you want to continue? (y/n): "
                )
                .strip()
                .lower()
            )
            if confirmation not in {"y", "yes"}:
                return

        try:
            with open(self.output_file, "r") as f:
                f.seek(0, 2)  # Move to the end of the file
                start_time = time.time()

                while seconds == 0 or (time.time() - start_time < seconds):
                    line = f.readline()
                    if line:
                        print(line, end="")
                    else:
                        time.sleep(0.1)  # 100ms delay between updates
        except KeyboardInterrupt:
            print("\nLive status updates stopped by user.")

    def _calculate_node_distribution(
        self, n_cores_required: int, tot_cores_per_node: int
    ) -> Tuple[int, int]:
        """Determine how many nodes and cores per node to request from a job scheduler.

        For example, if requiring 192 cores for a job on a system with 128 cores per node,
        this method advises requesting 2 nodes with 96 cores each.

        Parameters:
        -----------
        n_cores_required: int
            The number of cores required for the job
        tot_cores_per_node: int
            The number of cores per node on the target system

        Returns:
        --------
        n_nodes_to_request: int
            The number of nodes to request from the scheduler
        cores_to_request_per_node: int
            The number of cores per node to request from the scheduler
        """

        n_nodes_to_request = ceil(n_cores_required / tot_cores_per_node)
        cores_to_request_per_node = ceil(
            tot_cores_per_node
            - ((n_nodes_to_request * tot_cores_per_node) - n_cores_required)
            / n_nodes_to_request
        )

        return n_nodes_to_request, cores_to_request_per_node


class SlurmJob(SchedulerJob):
    @property
    def status(self):
        if self.id is None:
            return JobStatus.UNSUBMITTED
        else:
            sacct_cmd = f"sacct -j {self.id} --format=State%20 --noheader"
            result = subprocess.run(
                sacct_cmd, capture_output=True, text=True, shell=True
            )
            if result.returncode != 0:
                raise RuntimeError(
                    f"Failed to retrieve job status using {sacct_cmd}."
                    f"STDOUT: {result.stdout}, STDERR: {result.stderr}"
                )

        # Map sacct states to JobStatus enum
        sacct_status_map = {
            "PENDING": JobStatus.PENDING,
            "RUNNING": JobStatus.RUNNING,
            "COMPLETED": JobStatus.COMPLETED,
            "CANCELLED": JobStatus.CANCELLED,
            "FAILED": JobStatus.FAILED,
        }
        for state, status in sacct_status_map.items():
            if state in result.stdout:
                return status

        # Fallback if no known state is found
        return JobStatus.UNKNOWN

    @property
    def script(self):
        scheduler_script = "#!/bin/bash"
        scheduler_script += f"\n#SBATCH --job-name={self.job_name}"
        scheduler_script += f"\n#SBATCH --output={self.output_file}"
        if isinstance(self.queue, SlurmQOS):
            scheduler_script += f"\n#SBATCH --qos={self.queue_name}"
        elif isinstance(self.queue, SlurmPartition):
            scheduler_script += f"\n#SBATCH --partition={self.queue_name}"
        if self.scheduler.requires_task_distribution:
            scheduler_script += f"\n#SBATCH --nodes={self.nodes}"
            scheduler_script += f"\n#SBATCH --ntasks-per-node={self.cpus_per_node}"
        else:
            scheduler_script += f"\n#SBATCH --ntasks={self.cpus}"
        scheduler_script += f"\n#SBATCH --account={self.account_key}"
        scheduler_script += "\n#SBATCH --export=ALL"
        scheduler_script += "\n#SBATCH --mail-type=ALL"
        scheduler_script += f"\n#SBATCH --time={self.walltime}"
        for (
            key,
            value,
        ) in self.scheduler.other_scheduler_directives.items():
            scheduler_script += f"\n#SBATCH {key} {value}"

        # Add roms command to scheduler script
        scheduler_script += f"\n\n{self.commands}"
        return scheduler_script

    def submit(self):
        self.save_script()
        # remove any slurm variables in case submitting from inside another slurm job
        env_vars_to_exclude = []
        for k in os.environ.keys():
            if k.startswith("SLURM_"):
                if k not in {"SLURM_CONF", "SLURM_VERSION"}:
                    env_vars_to_exclude.append(k)

        slurm_env = {
            k: v for k, v in os.environ.items() if k not in env_vars_to_exclude
        }

        result = subprocess.run(
            f"sbatch {self.script_path}",
            shell=True,
            cwd=self.run_path,
            env=slurm_env,
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            raise RuntimeError(
                "Non-zero exit code when submitting job. STDERR: "
                + f"\n{result.stderr}"
            )

        # Extract the job ID from the output
        matches = re.search(r"Submitted batch job (\d+)", result.stdout)
        if matches:
            self._id = int(matches.group(1))
            return self._id
        else:
            raise RuntimeError(
                f"Failed to parse job ID from sbatch output: {result.stdout}"
            )

    def cancel(self):
        result = subprocess.run(
            f"scancel {self.id}",
            shell=True,
            cwd=self.run_path,
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            raise RuntimeError(
                "Non-zero exit code when cancelling job. STDERR: "
                + f"\n{result.stderr}"
            )
        else:
            print(f"Job {self.id} cancelled")


class PBSJob(SchedulerJob):
    @property
    def script(self):
        scheduler_script = "#PBS -S /bin/bash"
        scheduler_script += f"\n#PBS -N {self.job_name}"
        scheduler_script += f"\n#PBS -o {self.output_file}"
        scheduler_script += f"\n#PBS -A {self.account_key}"
        scheduler_script += f"\n#PBS -l select={self.nodes}:ncpus={self.cpus_per_node},walltime={self.walltime}"
        scheduler_script += f"\n#PBS -q {self.queue_name}"
        scheduler_script += "\n#PBS -j oe"
        scheduler_script += "\n#PBS -k eod"
        scheduler_script += "\n#PBS -V"
        for (
            key,
            value,
        ) in cstar_sysmgr.scheduler.other_scheduler_directives.items():
            scheduler_script += f"\n#PBS {key} {value}"
        scheduler_script += "\ncd ${PBS_O_WORKDIR}"

        scheduler_script += f"\n\n{self.commands}"
        return scheduler_script

    @property
    def status(self):
        if self.id is None:
            return "unsubmitted"

        qstat_cmd = f"qstat -x -f -F json {self.id}"
        result = subprocess.run(qstat_cmd, capture_output=True, text=True, shell=True)

        if result.returncode != 0:
            raise RuntimeError(
                f"Failed to retrieve job status using {qstat_cmd}."
                f"STDOUT: {result.stdout}, STDERR: {result.stderr}"
            )

        # Parse the JSON output
        try:
            job_data = json.loads(result.stdout)
            try:
                job_info = next(iter(job_data["Jobs"].values()))
            except StopIteration:
                raise RuntimeError(f"Job ID {self.id} not found in qstat output.")

            # Extract the job state
            job_state = job_info["job_state"]
            pbs_status_map = {
                "Q": JobStatus.PENDING,
                "R": JobStatus.RUNNING,
                "C": JobStatus.COMPLETED,
                "H": JobStatus.HELD,
                "E": JobStatus.ENDING,
            }

            # Handle specific cases for "F" (Finished)
            if job_state == "F":
                exit_status = job_info.get("Exit_status", 1)
                return JobStatus.COMPLETED if exit_status == 0 else JobStatus.FAILED
            else:
                # Default to UNKNOWN for unmapped states
                return pbs_status_map.get(job_state, JobStatus.UNKNOWN)

        except json.JSONDecodeError as e:
            raise RuntimeError(f"Failed to parse JSON from qstat output: {e}")

    def submit(self):
        self.save_script()

        result = subprocess.run(
            f"qsub {self.script_path}",
            shell=True,
            cwd=self.run_path,
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            raise RuntimeError(
                "Non-zero exit code when submitting job. STDERR: "
                + f"\n{result.stderr}"
            )

        # Validate the format of the job ID (e.g., "<int>.<str>")
        job_id_full = result.stdout.strip()  # Full job ID (e.g., "7063621.desched1")
        if not re.match(r"^\d+\.\w+$", job_id_full):
            raise RuntimeError(f"Unexpected job ID format from qsub: {job_id_full}")

        # Extract the job ID from the output
        self._id = int(result.stdout.strip().split(".")[0])
        return self._id

    def cancel(self):
        if self.status not in {JobStatus.RUNNING, JobStatus.PENDING, JobStatus.HELD}:
            print(f"Cannot cancel job with status {self.status}")
            return

        result = subprocess.run(
            f"qdel {self.id}",
            shell=True,
            cwd=self.run_path,
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            raise RuntimeError(
                "Non-zero exit code when cancelling job. STDERR: "
                + f"\n{result.stderr}"
            )
        else:
            print(f"Job {self.id} cancelled")