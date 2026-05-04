#!/usr/bin/env python3
"""
Utility per esplorare il contenuto dei file generati da Protobuf (_pb2.py).
Mostra i messaggi, gli enum e le costanti disponibili per l'importazione.
"""

import sys
import importlib
import inspect
from pathlib import Path
from google.protobuf import descriptor as _descriptor

# 1. Aggiungi la root del repo (per eventuali import tipo v2.xxx)
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# 2. Aggiungi anche la root dei proto (per import tipo oaa.xxx)
PROTO_ROOT = REPO_ROOT / "v2" / "protos"
if str(PROTO_ROOT) not in sys.path:
    sys.path.insert(0, str(PROTO_ROOT))

# Mappa i codici numerici dei tipi Protobuf in stringhe leggibili (es. 14 -> ENUM)
FIELD_TYPE_NAMES = {
    v: k.replace("TYPE_", "")
    for k, v in _descriptor.FieldDescriptor.__dict__.items()
    if isinstance(k, str) and k.startswith("TYPE_")
}

def print_message_details_recursive(descriptor, indent=2, visited=None):
    """Esplora ricorsivamente i campi di un messaggio."""
    if visited is None:
        visited = set()

    # Evita ricorsioni infinite in caso di messaggi circolari
    if descriptor.full_name in visited:
        print(f"{' ' * indent}└── {descriptor.name} (già visualizzato)")
        return
    visited.add(descriptor.full_name)

    for field in descriptor.fields:
        type_str = FIELD_TYPE_NAMES.get(field.type, str(field.type))
        type_info = ""
        if field.message_type:
            type_info = f" -> {field.message_type.full_name}"
        elif field.enum_type:
            type_info = f" -> {field.enum_type.full_name}"

        print(f"{' ' * indent}├── {field.name} (number={field.number}, type={type_str}{type_info})")

        # Se è un Enum, mostra i valori
        if field.enum_type:
            for enum_val in field.enum_type.values:
                print(f"{' ' * (indent + 4)}├── {enum_val.name} = {enum_val.number}")

        # Se è un messaggio, scendi ricorsivamente
        if field.message_type:
            print_message_details_recursive(field.message_type, indent + 4, visited)

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
            if hasattr(obj, 'DESCRIPTOR'):
                for enum_type in obj.DESCRIPTOR.enum_types:
                    print(f"  └─ {name}.{enum_type.name} (Enum)")
                    for enum_val in enum_type.values:
                        print(f"     ├── {enum_val.name} = {enum_val.number}")

    print(f"\n[Messaggi/Classi disponibili a livello top-level]:")
    if not messages:
        print("  (nessuno)")
    for msg in sorted(messages):
        print(f"  ├── {msg}")

    # --- NUOVA SEZIONE: dettagli dei messaggi ---
    for msg_name in sorted(messages):
        msg_cls = getattr(module, msg_name, None)
        if msg_cls is None or not hasattr(msg_cls, "DESCRIPTOR"):
            continue

        print(f"\n[Dettagli messaggio: {msg_name}]")
        print_message_details_recursive(msg_cls.DESCRIPTOR)

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