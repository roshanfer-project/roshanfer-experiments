#!/usr/bin/env python3
"""Test script for max-queue-motivation plugin."""

import sys
import os
import tempfile
import json
from pathlib import Path

# Add the experiments/exec directory to the path so we can import modules
current_dir = Path(__file__).parent
exec_dir = current_dir
sys.path.insert(0, str(exec_dir))

def create_mock_data():
    """Create mock data that resembles what the plugin would receive."""
    
    # Mock metric files for different loads and services
    mock_metrics_load300 = [
        {
            'queue_length_search-hotel_frontend': {
                'timestamps': [1.0, 2.0, 3.0, 4.0, 5.0],
                'values': [5.0, 8.0, 12.0, 7.0, 3.0]  # max = 12
            },
            'queue_length_search-hotel_search': {
                'timestamps': [1.0, 2.0, 3.0, 4.0, 5.0],
                'values': [2.0, 4.0, 6.0, 3.0, 1.0]  # max = 6
            }
        },
        # Second repeat for load 300
        {
            'queue_length_search-hotel_frontend': {
                'timestamps': [1.0, 2.0, 3.0, 4.0, 5.0],
                'values': [6.0, 9.0, 14.0, 8.0, 4.0]  # max = 14
            },
            'queue_length_search-hotel_search': {
                'timestamps': [1.0, 2.0, 3.0, 4.0, 5.0],
                'values': [3.0, 5.0, 8.0, 4.0, 2.0]  # max = 8
            }
        }
    ]
    
    mock_metrics_load800 = [
        {
            'queue_length_search-hotel_frontend': {
                'timestamps': [1.0, 2.0, 3.0, 4.0, 5.0],
                'values': [15.0, 18.0, 22.0, 17.0, 13.0]  # max = 22
            },
            'queue_length_search-hotel_search': {
                'timestamps': [1.0, 2.0, 3.0, 4.0, 5.0],
                'values': [8.0, 12.0, 16.0, 11.0, 7.0]  # max = 16
            }
        },
        # Second repeat for load 800
        {
            'queue_length_search-hotel_frontend': {
                'timestamps': [1.0, 2.0, 3.0, 4.0, 5.0],
                'values': [16.0, 20.0, 24.0, 19.0, 14.0]  # max = 24
            },
            'queue_length_search-hotel_search': {
                'timestamps': [1.0, 2.0, 3.0, 4.0, 5.0],
                'values': [9.0, 13.0, 18.0, 12.0, 8.0]  # max = 18
            }
        }
    ]
    
    unit_entries = [
        {
            'run_unit_name': 'rate-300',
            'load_value': 300,
            'repeat_metric_files': mock_metrics_load300
        },
        {
            'run_unit_name': 'rate-800', 
            'load_value': 800,
            'repeat_metric_files': mock_metrics_load800
        }
    ]
    
    return unit_entries

def test_plugin():
    """Test the max-queue-motivation plugin."""
    
    # Import the plugin
    try:
        from plots.plugins.max_queue_motivation_experiment import generate_experiment_plots
        print("✓ Successfully imported the plugin")
    except ImportError as e:
        print(f"✗ Failed to import plugin: {e}")
        return False
    
    # Create temporary output directory
    with tempfile.TemporaryDirectory() as temp_dir:
        output_dir = Path(temp_dir)
        
        # Create mock context
        unit_entries = create_mock_data()
        
        ctx = {
            'type': 'max-queue-motivation',
            'experiment_name': 'test-experiment',
            'unit_entries': unit_entries,
            'output_dir': output_dir,
            'apis': ['search-hotel']  # Exactly one API as required
        }
        
        print("Testing with valid context (1 API, 2 loads)...")
        
        try:
            # Test the plugin
            result_files = generate_experiment_plots(ctx)
            
            if result_files:
                print(f"✓ Plugin executed successfully, generated {len(result_files)} files:")
                for f in result_files:
                    print(f"  - {f}")
                    if f.exists():
                        print(f"    ✓ File exists (size: {f.stat().st_size} bytes)")
                    else:
                        print(f"    ✗ File does not exist")
            else:
                print("✗ Plugin returned no files")
                return False
                
        except Exception as e:
            print(f"✗ Plugin execution failed: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    # Test error cases
    print("\nTesting error cases...")
    
    # Test with multiple APIs (should fail)
    with tempfile.TemporaryDirectory() as temp_dir:
        output_dir = Path(temp_dir)
        ctx_multi_api = {
            'type': 'max-queue-motivation',
            'experiment_name': 'test-experiment',
            'unit_entries': unit_entries,
            'output_dir': output_dir,
            'apis': ['search-hotel', 'reserve-hotel']  # Two APIs - should fail
        }
        
        try:
            result_files = generate_experiment_plots(ctx_multi_api)
            print("✗ Should have failed with multiple APIs")
            return False
        except ValueError as e:
            if "only supports exactly one API" in str(e):
                print("✓ Correctly rejected multiple APIs")
            else:
                print(f"✗ Wrong error for multiple APIs: {e}")
                return False
    
    # Test with wrong number of loads (should fail)
    with tempfile.TemporaryDirectory() as temp_dir:
        output_dir = Path(temp_dir)
        unit_entries_one_load = [unit_entries[0]]  # Only one load
        ctx_one_load = {
            'type': 'max-queue-motivation',
            'experiment_name': 'test-experiment', 
            'unit_entries': unit_entries_one_load,
            'output_dir': output_dir,
            'apis': ['search-hotel']
        }
        
        try:
            result_files = generate_experiment_plots(ctx_one_load)
            print("✗ Should have failed with wrong number of loads")
            return False
        except ValueError as e:
            if "assumes exactly 2 loads" in str(e):
                print("✓ Correctly rejected wrong number of loads")
            else:
                print(f"✗ Wrong error for load count: {e}")
                return False
    
    print("\n✓ All tests passed!")
    return True

if __name__ == "__main__":
    success = test_plugin()
    sys.exit(0 if success else 1)
