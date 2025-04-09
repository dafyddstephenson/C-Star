import os
import shutil
from pathlib import Path
from cstar.base.external_codebase import ExternalCodeBase
from cstar.base.utils import _clone_and_checkout, _run_cmd, _update_user_dotenv
from cstar.system.manager import cstar_sysmgr


class ROMSExternalCodeBase(ExternalCodeBase):
    """An implementation of the ExternalCodeBase class for the UCLA Regional Ocean
    Modeling System.

    This subclass sets unique values for ExternalCodeBase properties specific to ROMS, and overrides
    the get() method to compile ROMS-specific libraries.

    Methods:
    --------
    get()
        overrides ExternalCodeBase.get() to clone the UCLA ROMS repository, set environment, and compile libraries
    """

    @property
    def default_source_repo(self) -> str:
        return "https://github.com/CESR-lab/ucla-roms.git"

    @property
    def default_checkout_target(self) -> str:
        return "main"

    @property
    def expected_env_var(self) -> str:
        return "ROMS_ROOT"

    def _codebase_adjustments(self) -> None:
        """Perform C-Star specific adjustments to stock ROMS code.

        In particular, this method replaces the default Makefiles with machine-agnostic
        versions, allowing C-Star to be used with ROMS across multiple different
        computing systems.
        """
        shutil.copytree(
            Path(os.environ[self.expected_env_var]) / "ci/ci_makefiles/",
            os.environ[self.expected_env_var],
            dirs_exist_ok=True,
        )

    def get(self, target: str | Path) -> None:
        """Clone ROMS code to local machine, set environment, compile libraries.

        This method:
        1. clones ROMS from `source_repo`
        2. checks out the correct commit from `checkout_target`
        3. Sets environment variable ROMS_ROOT and appends $ROMS_ROOT/Tools-Roms to PATH
        4. Replaces ROMS Makefiles for machine-agnostic compilation
        5. Compiles the NHMG library
        6. Compiles the Tools-Roms package

        Parameters:
        -----------
        target: src
            the path where ROMS will be cloned and compiled
        """
        target = Path(target).expanduser()
        # TODO: Situation where environment variables like ROMS_ROOT are not set...
        # ... but repo already exists at local_path results in an error rather than a prompt
        _clone_and_checkout(
            source_repo=self.source_repo,
            local_path=target,
            checkout_target=self.checkout_target,
        )

        # Set environment variables for this session:
        os.environ["ROMS_ROOT"] = str(target)
        cstar_sysmgr.environment.environment_variables["ROMS_ROOT"] = os.environ[
            "ROMS_ROOT"
        ]
        os.environ["PATH"] = os.environ.get("PATH", "") + f":{target}/Tools-Roms/"
        # os.environ["PATH"] += f":{target}/Tools-Roms/"
        cstar_sysmgr.environment.environment_variables["PATH"] = os.environ["PATH"]
        env_file_str = (
            f"ROMS_ROOT={target}" + "\nPATH=${PATH}:" + f"{target}/Tools-Roms\n"
        )
        _update_user_dotenv(env_file_str)

        # Distribute custom makefiles for ROMS
        self._codebase_adjustments()

        # Make things
        _run_cmd(
            f"make nhmg COMPILER={cstar_sysmgr.environment.compiler}",
            cwd=target / "Work",
            msg_pre="Compiling NHMG library...",
            msg_err="Error when compiling ROMS' NHMG library.",
            raise_on_error=True,
        )
        _run_cmd(
            f"make COMPILER={cstar_sysmgr.environment.compiler}",
            cwd=target / "Tools-Roms",
            msg_pre="Compiling Tools-Roms package for UCLA ROMS...",
            msg_post=f"UCLA-ROMS is installed at {target}",
            msg_err="Error when compiling Tools-Roms.",
            raise_on_error=True,
        )
