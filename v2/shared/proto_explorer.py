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

# Tipi di campo Protobuf (mappa i codici numerici in stringhe leggibili)
FIELD_TYPE_NAMES = {
    _descriptor.FieldDescriptor.TYPE_DOUBLE: "DOUBLE",
    _descriptor.FieldDescriptor.TYPE_FLOAT: "FLOAT",
    _descriptor.FieldDescriptor.TYPE_INT64: "INT64",
    _descriptor.FieldDescriptor.TYPE_UINT64: "UINT64",
    _descriptor.FieldDescriptor.TYPE_INT32: "INT32",
    _descriptor.FieldDescriptor.TYPE_FIXED64: "FIXED64",
    _descriptor.FieldDescriptor.TYPE_FIXED32: "FIXED32",
    _descriptor.FieldDescriptor.TYPE_BOOL: "BOOL",
    _descriptor.FieldDescriptor.TYPE_STRING: "STRING",
    _descriptor.FieldDescriptor.TYPE_GROUP: "GROUP",
    _descriptor.FieldDescriptor.TYPE_MESSAGE: "MESSAGE",
    _descriptor.FieldDescriptor.TYPE_BYTES: "BYTES",
    _descriptor.FieldDescriptor.TYPE_UINT32: "UINT32",
    _descriptor.FieldDescriptor.TYPE_ENUM: "ENUM",
    _descriptor.FieldDescriptor.TYPE_SFIXED32: "SFIXED32",
    _descriptor.FieldDescriptor.TYPE_SFIXED64: "SFIXED64",
    _descriptor.FieldDescriptor.TYPE_SINT32: "SINT32",
    _descriptor.FieldDescriptor.TYPE_SINT64: "SINT64",
}

# Label dei campi (1=OPTIONAL, 2=REQUIRED, 3=REPEATED)
FIELD_LABEL_NAMES = {
    _descriptor.FieldDescriptor.LABEL_OPTIONAL: "OPTIONAL",
    _descriptor.FieldDescriptor.LABEL_REQUIRED: "REQUIRED",
    _descriptor.FieldDescriptor.LABEL_REPEATED: "REPEATED",
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

        # Risoluzione della label del campo evitando il DeprecationWarning.
        # Nelle versioni recenti di Protobuf si consiglia l'uso di is_repeated o is_required.
        if hasattr(field, 'is_repeated'):
            label_str = "REPEATED" if field.is_repeated else ("REQUIRED" if field.is_required else "OPTIONAL")
        else:
            # Fallback legacy per versioni molto vecchie di protobuf
            label_str = FIELD_LABEL_NAMES.get(field.label, str(field.label))

        type_info = ""
        if field.message_type:
            # Get the Python module path from the proto file
            proto_file = field.message_type.file.name
            module_path = proto_file.replace('/', '.').replace('.proto', '_pb2')
            class_name = field.message_type.full_name.split('.')[-1]
            python_location = f"{module_path}.{class_name}"
            type_info = f" -> {field.message_type.full_name} (from {python_location})"
        elif field.enum_type:
            # Get the Python module path from the proto file
            proto_file = field.enum_type.file.name
            module_path = proto_file.replace('/', '.').replace('.proto', '_pb2')
            enum_name = field.enum_type.full_name.split('.')[-1]
            python_location = f"{module_path}.{enum_name}"
            type_info = f" -> {field.enum_type.full_name} (from {python_location})"

        print(f"{' ' * indent}├── {field.name} (number={field.number}, type={type_str}{type_info}, label={label_str})")

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