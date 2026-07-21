import sys
import os
import subprocess
import time
import signal

PYTHON_PATH = "/home/nemo/miniconda3/envs/py314/bin/python"
if not os.path.exists(PYTHON_PATH):
    PYTHON_PATH = sys.executable

def main():
    # Enforce run inside correct directory
    workspace_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(workspace_dir)
    
    print("[Orchestrator] Starting multiprocess UI integration system...")
    
    processes = []
    
    try:
        # 1. Start Main Window
        print("[Orchestrator] Launching Main Window...")
        main_proc = subprocess.Popen([PYTHON_PATH, "-u", "main_window.py"])
        processes.append(main_proc)
        
        # Give ZMQ server on main window a brief moment to start binding
        time.sleep(0.5)
        
        # 2. Start offscreen processes
        print("[Orchestrator] Launching Interactive Form Widget process...")
        form_proc = subprocess.Popen([
            PYTHON_PATH, "-u", "offscreen_widget.py",
            "--widget-id", "interactive_form",
            "--docking", "left",
            "--z-index", "0",
            "--priority", "10",
            "--min-width", "350",
            "--aspect-ratio", "1.0"
        ])
        processes.append(form_proc)
        
        print("[Orchestrator] Launching Bouncing Ball Animation Widget process...")
        ball_proc = subprocess.Popen([
            PYTHON_PATH, "-u", "offscreen_widget.py",
            "--widget-id", "bouncing_ball",
            "--docking", "right",
            "--z-index", "0",
            "--priority", "5",
            "--min-width", "450",
            "--aspect-ratio", "1.33"
        ])
        processes.append(ball_proc)

        print("[Orchestrator] Launching Status Overlay Widget process...")
        overlay_proc = subprocess.Popen([
            PYTHON_PATH, "-u", "offscreen_widget.py",
            "--widget-id", "status_overlay",
            "--docking", "top-right",
            "--z-index", "1",
            "--priority", "100",
            "--max-width", "280",
            "--max-height", "100",
            "--aspect-ratio", "2.8"
        ])
        processes.append(overlay_proc)
        
        # 3. Monitor lifecycle until main window exits
        print("[Orchestrator] System running. Monitor loop active (Close Main Window or hit Ctrl+C to stop)...")
        warned = {"form": False, "ball": False, "overlay": False}
        while main_proc.poll() is None:
            if not warned["form"] and form_proc.poll() is not None:
                print("[Orchestrator] WARNING: Interactive Form process crashed/exited!")
                warned["form"] = True
            if not warned["ball"] and ball_proc.poll() is not None:
                print("[Orchestrator] WARNING: Bouncing Ball process crashed/exited!")
                warned["ball"] = True
            if not warned["overlay"] and overlay_proc.poll() is not None:
                print("[Orchestrator] WARNING: Status Overlay process crashed/exited!")
                warned["overlay"] = True
            time.sleep(0.2)
            
        print("[Orchestrator] Main Window closed by user.")
        
    except KeyboardInterrupt:
        print("\n[Orchestrator] Interrupted by user.")
    finally:
        # Clean up child processes
        print("[Orchestrator] Terminating processes...")
        for proc in processes:
            if proc.poll() is None:
                try:
                    proc.terminate()
                    proc.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    
        # Explicit cleanup of shared memory files in /dev/shm to prevent resource leaks
        print("[Orchestrator] Unlinking shared memory segments from /dev/shm...")
        for widget_id in ["interactive_form", "bouncing_ball", "status_overlay"]:
            for idx in [0, 1]:
                shm_path = f"/dev/shm/poc_shm_{widget_id}_buf_{idx}"
                if os.path.exists(shm_path):
                    try:
                        os.unlink(shm_path)
                        print(f"[Orchestrator] Unlinked leftover SHM file: {shm_path}")
                    except Exception as e:
                        print(f"[Orchestrator] Error unlinking SHM file {shm_path}: {e}")
                        
        print("[Orchestrator] Shutdown complete.")

if __name__ == "__main__":
    main()
