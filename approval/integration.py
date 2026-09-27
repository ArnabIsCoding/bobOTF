import sys
sys.stdout.reconfigure(encoding='utf-8')
import os
import json

# Setup paths to ensure we can import from other device directories
HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(HERE)

sys.path.append(os.path.join(PROJECT_ROOT, "instinct"))
sys.path.append(os.path.join(PROJECT_ROOT, "memory"))
sys.path.append(os.path.join(PROJECT_ROOT, "hands"))

from detector import Instinct
from retrieve import retrieve
from hands.compiler import compile_fix
from approval import request_approval

def run_pipeline():
    telemetry_file = os.path.join(PROJECT_ROOT, "ears", "telemetry_stream.jsonl")
    
    print("[Pipeline] Initialising Instinct (Device 2) with tick_seconds=1")
    instinct = Instinct(tick_seconds=1)
    
    print(f"[Pipeline] Reading telemetry from {telemetry_file}")
    
    anomaly_detected = False
    
    with open(telemetry_file, "r") as f:
        for line_idx, line in enumerate(f):
            schema1_dict = json.loads(line)
            
            # DEVICE 1 -> DEVICE 2
            schema2_dict = instinct.process_tick(schema1_dict)
            
            if schema2_dict:
                print(f"\n[Pipeline] Anomaly detected at telemetry line {line_idx+1}!")
                print(f"[Pipeline] Anomaly details: {schema2_dict['description']}")
                
                # DEVICE 2 -> DEVICE 3
                print("[Pipeline] Querying Memory (Device 3) for fix procedure...")
                try:
                    schema3_dict = retrieve(schema2_dict)
                    print(f"[Pipeline] Found procedure: {schema3_dict['procedure_id']} (Confidence: {schema3_dict['confidence']:.2f})")
                    print(f"[Pipeline] Suggested fix: {schema3_dict['suggested_fix_nl']}")
                except Exception as e:
                    print(f"[Pipeline] Device 3 error: {e}")
                    return

                # DEVICE 3 -> DEVICE 4
                print("[Pipeline] Compiling fix (Device 4)...")
                try:
                    schema4_dict = compile_fix(schema3_dict)
                    if schema4_dict['validation']['passed']:
                        print("[Pipeline] Compilation and validation successful.")
                    else:
                        print("[Pipeline] Warning: Validation failed for compiled program.")
                except Exception as e:
                    print(f"[Pipeline] Device 4 error: {e}")
                    return

                # DEVICE 4 -> DEVICE 5
                print("[Pipeline] Requesting approval (Device 5)...")
                schema5_dict = request_approval(schema4_dict)
                
                print("\n[Pipeline] Final output (Schema 5):")
                print(json.dumps(schema5_dict, indent=2))
                
                anomaly_detected = True
                break # We just process the first anomaly and run it end-to-end
                
    if not anomaly_detected:
        print("[Pipeline] No anomaly detected in the telemetry stream.")

if __name__ == "__main__":
    run_pipeline()
