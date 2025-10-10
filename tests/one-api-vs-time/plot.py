import os
import sys

import pandas as pd
from canvas import canvas
import numpy as np
from math import ceil


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


    # Create canvas
    fig, ax = canvas.create_canvas(width_in_inches=3.33,
                                   marker_size=1,
                                   line_width=2,
                                   font_size=12,
                                   legend_size=12)

    markers = canvas.marker_list
    colors = canvas.color_list

    latency_metrics = [
        "p50_latency",
        "p95_latency",
    ]

    # stack metrics
    for i, metric in enumerate(latency_metrics):
        metric_df = data[['relative_time', metric]]
        relative_time = metric_df['relative_time']
        ax.plot(relative_time, 
                metric_df[metric], 
                label=name_to_label(metric), 
                color=colors[i % len(colors)],
                marker=markers[i % len(markers)])
    
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Latency (ms)")
    ax.legend(loc='upper left')
    ax.grid(True, alpha=0.3)

    # Set x-axis ticks (even numbers only)
    max_time = data['relative_time'].max()
    ax.set_xticks(np.arange(0, ceil(max_time) + 2, 2))
    ax.set_xticklabels([str(int(x)) for x in np.arange(0, ceil(max_time) + 2, 2)])
    ax.set_xlim(0, max_time)

    # draw a horizontal line at y=60
    ax.axhline(y=60, color='r', linestyle='--', label='SLO')

    # make y-axis log scale
    ax.set_yscale('log')

    # Set y-axis limit
    ax.set_ylim(1, 500)

    # Save plot
    fig.savefig(f'tests/one-api-vs-time/latency.pdf', bbox_inches='tight')


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
    # Create a new canvas for rates
    fig_r, ax_r = canvas.create_canvas(width_in_inches=3.33,
                                       marker_size=1,
                                       line_width=0.5,
                                       font_size=12,
                                       legend_size=12)

    # Custom color mapping: bad metrics get warning/error colors
    color_mapping = {
        'goodput': '#2ca02c',           # Green for good metric
        'slo_violations': '#d62728',    # Red for bad metric (SLO violations)
        'dropped_requests': '#ff7f0e',  # Orange for bad metric (drops)
        # kept a fallback key in case other names are used elsewhere
        'dropped': '#a5b41f'
    }
    
    alpha_mapping = {
        'goodput': 0.6,
        'slo_violations': 0.8,   # More opaque for better visibility of bad metric
        'dropped_requests': 0.8, # More opaque for better visibility of bad metric
        'dropped': 0.6
    }

    x = data['relative_time'] if 'relative_time' in data.columns else None
    if x is None:
        raise ValueError("Data must contain 'relative_time' column for x-axis.")
    else:
        # Prepare stacked series: ensure each metric exists and has same length
        stacked_values = []
        stacked_labels = []
        stacked_colors = []
        stacked_alphas = []

        for metric in stack_metrics:
            if metric in data.columns:
                vals = data[metric].values.tolist()
            else:
                raise ValueError(f"Data must contain '{metric}' column for stacking.")
            stacked_values.append([v / 1000.0 for v in vals])  # convert to KRPS
            stacked_labels.append(metric)
            stacked_colors.append(color_mapping.get(metric, None))
            stacked_alphas.append(alpha_mapping.get(metric, 0.6))

        # Plot stacked areas
        cumulative = None
        for i, (values, label, color, alpha) in enumerate(zip(stacked_values, stacked_labels, stacked_colors, stacked_alphas)):
            if cumulative is None:
                ax_r.fill_between(x, 0, values, label=name_to_label(label),
                                  color=color, alpha=alpha)
                cumulative = values
            else:
                new_cumulative = [a + b for a, b in zip(cumulative, values)]
                ax_r.fill_between(x, cumulative, new_cumulative, label=name_to_label(label),
                                  color=color, alpha=alpha)
                cumulative = new_cumulative

        # Plot total_requests (offered load) as a dashed black line (in KRPS)
        if 'total_requests' in data.columns:
            offered_krps = data['total_requests'] / 1000.0
            ax_r.plot(x, offered_krps, label=name_to_label('total_requests'), color='k', linestyle='--', linewidth=2)

        ax_r.set_xlabel('Time (s)')
        ax_r.set_ylabel('Rate (KRPS)')
        ax_r.legend(loc='upper left')
        ax_r.grid(True, alpha=0.3)

        # x-axis ticks (even numbers)
        max_time = data['relative_time'].max()
        ax_r.set_xticks(np.arange(0, ceil(max_time) + 2, 2))
        ax_r.set_xticklabels([str(int(t)) for t in np.arange(0, ceil(max_time) + 2, 2)])
        ax_r.set_xlim(0, max_time)

        # y limits: compute maximum across stacked values and offered load
        max_y_value = 0.0
        for vals in stacked_values:
            if vals:
                max_y_value = max(max_y_value, max(vals))
        if 'total_requests' in data.columns:
            max_y_value = max(max_y_value, (data['total_requests'] / 1000.0).max())

        if max_y_value > 0:
            ax_r.set_ylim(0, max_y_value * 1.2)

        # Save rate plot
        fig_r.savefig('tests/one-api-vs-time/rate.pdf', bbox_inches='tight')

if __name__ == "__main__":
    main()