import os

def genera_lista_file(folder_path, output_file):
    """
    Genera un file .lst contenente i percorsi relativi di tutti i file
    presenti in una directory specificata.

    Args:
        folder_path (str): Percorso della cartella da analizzare.
        output_file (str): Nome del file .lst da generare.
    """
    # Controlla se il percorso fornito è una directory valida
    if not os.path.isdir(folder_path):
        raise ValueError(f"Il percorso {folder_path} non è una directory valida.")

    # Apri il file di output in modalità scrittura
    with open(output_file, 'w') as f:
        # Itera su tutti i file nella directory
        for file_name in os.listdir(folder_path):
            # Costruisci il percorso completo del file
            full_path = os.path.join(folder_path, file_name)
            # Verifica se è un file (esclude le sottodirectory)
            if os.path.isfile(full_path):
                # Scrivi il percorso relativo nel file di output
                f.write(f"{folder_path}/{file_name}\n")

# Esempio di utilizzo
folder = 'PIDNet/data/loveDa/Test/Rural/images_png'  # Sostituisci con il tuo percorso
output = 'PIDNet/data/list/loveda/test.lst'         # Nome del file di output
genera_lista_file(folder, output)
