import os
import sys

import pandas as pd
import numpy as np
from math import ceil
from pathlib import Path

# Import plotting primitives
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
from exec.plots.plotting_primitives import SubplotGrid, ACM_COMPACT_HALF, plot_line, plot_stacked_area, ACM_QUARTER


def read_realtime_data(file_path):
    """
    sample:

    timestamp,relative_time,goodput,slo_violations,dropped_requests,errors,p50_latency,p95_latency,total_requests
    2025-10-07T14:23:33.000735133+00:00,0.0,2340.0,0.0,770.0,0.0,15.591000000000001,45.8248,311
    2025-10-07T14:23:33.100735133+00:00,0.1,3110.0,0.0,0.0,0.0,3.944,4.918,311
    """

    df = pd.read_csv(os.path.join(os.path.dirname(__file__), file_path))

    # Parse timestamp if present and normalize relative_time
    if 'timestamp' in df.columns:
        df['timestamp'] = pd.to_datetime(df['timestamp'], errors='coerce')
        if 'relative_time' not in df.columns or df['relative_time'].isnull().all():
            start = df['timestamp'].min()
            df['relative_time'] = (df['timestamp'] - start).dt.total_seconds()

    # Ensure numeric columns are numeric
    for col in ['relative_time', 'goodput', 'slo_violations', 'dropped_requests', 'errors',
                'p50_latency', 'p95_latency', 'total_requests']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')

    # Fill any remaining NaNs in numeric columns with 0 to make plotting robust
    num_cols = df.select_dtypes(include=[float, int]).columns
    df[num_cols] = df[num_cols].fillna(0.0)

    return df

def name_to_label(name):
    mapping = {
        "goodput": "Goodput",
        "slo_violations": "SLO Violations",
        "dropped_requests": "Dropped Requests",
        "errors": "Errors",
        "p50_latency": "P50 Latency",
        "p95_latency": "P95 Latency",
        "total_requests": "Load"
    }
    return mapping[name]

def main():
    # Load collected data from file
    data = read_realtime_data("realtime.csv")
    print("Data loaded from realtime.csv")


    # Create latency plot using plotting primitives
    grid = SubplotGrid(ACM_COMPACT_HALF, layout="1x1")
    ax = grid.get_ax(0, 0)

    latency_metrics = [
        "p50_latency",
        "p95_latency",
    ]

    # Plot latency metrics using plot_line
    for i, metric in enumerate(latency_metrics):
        relative_time = data['relative_time']
        plot_line(ax, relative_time, data[metric], 
                 label=name_to_label(metric), 
                 style=grid.style,
                 color_idx=i)
    
    # Configure axis with y-axis limits
    grid.configure_ax(ax,
                    x_data=data['relative_time'],
                    x_step=3,
                    x_type="int",
                     xlabel="Time (s)", 
                     ylabel="Latency (ms)",
                     log_y=True,
                     ylim=(1, 200))

    # draw a horizontal line at y=60
    ax.axhline(y=60, color='r', linestyle='--', label='SLO')

    # Add legend
    grid.add_shared_legend(position="top", two_rows=False, y_offset=1.05)

    # Save plot
    grid.save(Path('tests/one-api-vs-time/latency.pdf'))


    ########## rates ##########

    stack_metrics = [
        "goodput",
        "slo_violations",
        "dropped_requests",
    ]

    plot_metrics = [
        "total_requests"
    ]

    # ------- Rate plotting (stacked) -------
    # Create a new grid for rates
    grid_r = SubplotGrid(ACM_COMPACT_HALF, layout="1x1")
    ax_r = grid_r.get_ax(0, 0)

    # Custom color mapping: bad metrics get warning/error colors
    color_mapping = {
        'goodput': '#2ca02c',           # Green for good metric
        'slo_violations': '#d62728',    # Red for bad metric (SLO violations)
        'dropped_requests': '#ff7f0e',  # Orange for bad metric (drops)
        # kept a fallback key in case other names are used elsewhere
        'dropped': '#a5b41f'
    }

    x = data['relative_time'] if 'relative_time' in data.columns else None
    if x is None:
        raise ValueError("Data must contain 'relative_time' column for x-axis.")
    else:
        # Prepare stacked series data for plot_stacked_area
        y_series = {}
        for metric in stack_metrics:
            if metric in data.columns:
                # Convert to KRPS and use display label as key
                y_series[name_to_label(metric)] = data[metric].values / 1000.0
            else:
                raise ValueError(f"Data must contain '{metric}' column for stacking.")

        # Create color map using display labels
        display_color_map = {}
        for metric in stack_metrics:
            display_label = name_to_label(metric)
            display_color_map[display_label] = color_mapping.get(metric, '#999999')

        # Plot stacked areas using plotting primitives
        plot_stacked_area(ax_r, x, y_series, 
                         style=grid_r.style,
                         color_map=display_color_map)

        # Plot total_requests (offered load) as a dashed black line (in KRPS)
        if 'total_requests' in data.columns:
            offered_krps = data['total_requests'] / 1000.0
            plot_line(ax_r, x, offered_krps, 
                     label=name_to_label('total_requests'), 
                     style=grid_r.style,
                     color='k', linestyle='--')
        # y limits: compute maximum across stacked values and offered load
        max_y_value = 0.0
        for vals in y_series.values():
            if len(vals) > 0:
                max_y_value = max(max_y_value, vals.max())
        if 'total_requests' in data.columns:
            max_y_value = max(max_y_value, (data['total_requests'] / 1000.0).max())

        # Configure axis with automatic tick configuration
        grid_r.configure_ax(ax_r, 
                           xlabel='Time (s)', 
                           ylabel='Rate (KRPS)',
                           x_data=data['relative_time'],
                           x_step=3,
                           x_type="int",
                           ylim=(0, max_y_value * 1.05),
                           y_step=2,
                           y_type="int")

        # Add legend
        grid_r.add_shared_legend(position="top", two_rows=True, y_offset=1.15)


        # Save rate plot
        grid_r.save(Path('tests/one-api-vs-time/rate.pdf'))

if __name__ == "__main__":
    main()