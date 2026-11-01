"""Copy the specification into an isolated directory and rerun static validators."""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
def main():
    # A named temporary root; verify the final cleanup target is within it on Windows.
    temp_root=(Path(tempfile.gettempdir())/'agent-spec-handoff-checks').resolve()
    temp_root.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='spec-',dir=temp_root) as name:
        cleanup_target=Path(name).resolve()
        assert cleanup_target.parent==temp_root
        copy_root=cleanup_target/'spec'
        shutil.copytree(ROOT,copy_root,ignore=shutil.ignore_patterns('.venv','.deps','__pycache__','.git'))
        outputs=[]
        for script in ['validate_contracts.py','validate_handoff.py']:
            result=subprocess.run([sys.executable,str(copy_root/'tools'/script)],cwd=copy_root,text=True,capture_output=True,encoding='utf-8')
            outputs.append(result.stdout)
            if result.returncode:
                print(result.stdout);print(result.stderr);raise SystemExit(result.returncode)
        output='PASS isolated specification copy: both static validators succeeded without original specification documents or machine-local skills; existing validator environment was reused.\n'
        output+='NOT a fresh-model implementation test, dependency reinstall, SQL execution or runtime acceptance.\n'
        output+='\n'.join(outputs)
        (ROOT/'package-validation-results.txt').write_text(output,encoding='utf-8')
        print(output)
        assert cleanup_target.parent==temp_root

if __name__=='__main__':main()
