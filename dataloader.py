import os
from typing import List, Dict, Any
import json
import csv

class DataLoader:
    """A class to handle loading data from various file formats."""
    
    def __init__(self, base_path: str = "."):
        """
        Initialize the DataLoader.
        
        Args:
            base_path: Base directory path for loading files
        """
        self.base_path = base_path
    
    def load_json(self, filename: str) -> Dict[str, Any]:
        """
        Load data from a JSON file.
        
        Args:
            filename: Name of the JSON file
            
        Returns:
            Dictionary containing the loaded data
        """
        filepath = os.path.join(self.base_path, filename)
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    def load_text(self, filename: str) -> str:
        """
        Load data from a text file.
        
        Args:
            filename: Name of the text file
            
        Returns:
            String containing the file contents
        """
        filepath = os.path.join(self.base_path, filename)
        with open(filepath, 'r', encoding='utf-8') as f:
            return f.read()
    
    def load_lines(self, filename: str) -> List[str]:
        """
        Load lines from a text file.
        
        Args:
            filename: Name of the text file
            
        Returns:
            List of strings, one per line
        """
        filepath = os.path.join(self.base_path, filename)
        with open(filepath, 'r', encoding='utf-8') as f:
            return [line.strip() for line in f.readlines()]
    
    def load_csv(self, filename: str) -> List[List[str]]:
        """
        Load data from a CSV file.
        
        Args:
            filename: Name of the CSV file
            
        Returns:
            List of lists containing CSV data
        """
        filepath = os.path.join(self.base_path, filename)
        with open(filepath, 'r', encoding='utf-8') as f:
            reader = csv.reader(f)
            return list(reader)