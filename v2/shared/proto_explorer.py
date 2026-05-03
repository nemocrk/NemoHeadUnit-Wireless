#!/usr/bin/env python3
"""
Utility per esplorare il contenuto dei file generati da Protobuf (_pb2.py).
Mostra i messaggi, gli enum e le costanti disponibili per l'importazione.
"""

import sys
import importlib
import inspect
from pathlib import Path

# Aggiungiamo la root del progetto al sys.path per permettere gli import
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

def explore_proto_module(module_name: str):
    """Importa un modulo e ne analizza la struttura Protobuf."""
    print(f"\n{'='*60}")
    print(f" Esplorazione modulo: {module_name}")
    print(f"{'='*60}")

    try:
        module = importlib.import_module(module_name)
    except ImportError as e:
        print(f"Errore: Impossibile importare il modulo. {e}")
        return

    # 1. Cerca il DESCRIPTOR del modulo
    if hasattr(module, 'DESCRIPTOR'):
        print(f"[Module Descriptor]: {module.DESCRIPTOR.name}")

    # 2. Analizza i membri del modulo
    members = inspect.getmembers(module)
    
    messages = []
    enums = []

    for name, obj in members:
        if name.startswith('_'):
            continue
        
        # Identifica le classi dei messaggi (solitamente sottoclassi di google.protobuf.message.Message)
        if inspect.isclass(obj):
            messages.append(name)
            # Controlla se ci sono Enum annidati dentro il messaggio
            for sub_name, sub_obj in inspect.getmembers(obj):
                if sub_name == "Enum" or (inspect.isclass(sub_obj) and hasattr(sub_obj, 'DESCRIPTOR')):
                    if hasattr(sub_obj, 'Name'): # È un Enum
                        print(f"  └─ {name}.{sub_name} (Enum)")
                        # Mostra i valori dell'enum
                        if hasattr(sub_obj, 'keys'):
                            for key in sub_obj.keys():
                                print(f"     ├── {key}")

    print(f"\n[Messaggi/Classi disponibili a livello top-level]:")
    if not messages:
        print("  (nessuno)")
    for msg in sorted(messages):
        print(f"  ├── {msg}")

    print(f"\n[Esempio di importazione]:")
    if messages:
        print(f"  from {module_name} import {messages[0]}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Utilizzo: python proto_explorer.py <nome_modulo_pb2>")
        print("Esempio: python v2/shared/proto_explorer.py v2.protos.oaa.control.ControlMessageIdsEnum_pb2")
        sys.exit(1)

    target_module = sys.argv[1]
    # Rimuove l'estensione .py se presente
    if target_module.endswith(".py"):
        target_module = target_module[:-3].replace("/", ".")
    
    # Rimuove eventuali slash trasformandoli in punti per l'import
    target_module = target_module.replace("/", ".")
    if target_module.startswith("."): target_module = target_module[1:]

    explore_proto_module(target_module)