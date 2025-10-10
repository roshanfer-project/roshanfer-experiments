import matplotlib.pyplot as plt
import seaborn as sns

marker_list = ['o', 's', '^', 'D', 'v']
color_list = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd']

def create_canvas(nrows=1, ncols=1, width_in_inches=3.33, aspect_ratio=0.618, dpi=300, style="seaborn-v0_8-paper", 
                 font_size=16, title_size=16, label_size=16, legend_size=14, 
                 line_width=1.5, marker_size=4, compact=True):
    """
    Creates a consistent canvas for plotting with customizable parameters.

    Parameters:
        nrows (int): Number of rows of subplots.
        ncols (int): Number of columns of subplots.
        width_in_inches (float): Width of the figure in inches (default is 3.33 for half-column).
        aspect_ratio (float): Height-to-width ratio (default is golden ratio ~0.618).
        dpi (int): Resolution of the figure in dots per inch (default is 300).
        style (str): Matplotlib style (default is "seaborn-v0_8-paper").
        font_size (int): Base font size (default is 10).
        title_size (int): Title font size (default is 12).
        label_size (int): Label font size (default is 10).
        legend_size (int): Legend font size (default is 8).
        line_width (float): Line width (default is 1.5).
        marker_size (float): Marker size (default is 6).
    
    Returns:
        fig, ax: Matplotlib figure and axes objects.
    """
    # Set style first
    plt.style.use(style)
    
    # Create figure with fixed dimensions
    height_in_inches = width_in_inches * aspect_ratio * nrows
    total_width_in_inches = width_in_inches * ncols
    
    fig, axes = plt.subplots(nrows, ncols, figsize=(total_width_in_inches, height_in_inches), dpi=dpi)
    
    # Adaptive margins: tighter for multi-column to make figure appear more compact proportionally
    if compact and ncols > 1:
        # Reduce side margins and horizontal spacing
        left = 0.07 if ncols > 2 else 0.08
        right = 0.995
        bottom = 0.13
        top = 0.94
        wspace = 0.18 if ncols > 2 else 0.22
        hspace = 0.15
        fig.subplots_adjust(left=left, right=right, top=top, bottom=bottom, wspace=wspace, hspace=hspace)
    else:
        fig.subplots_adjust(left=0.15, right=0.95, top=0.95, bottom=0.15)
    
    # If there's only one subplot, axes is not an array, so make it one
    if nrows == 1 and ncols == 1:
        axes = [axes]
    else:
        axes = axes.flatten()

    for ax in axes:
        # Apply custom parameters
        ax.tick_params(labelsize=font_size)
        ax.xaxis.label.set_size(label_size)
        ax.yaxis.label.set_size(label_size)
        
        # Set default line and marker properties
        ax.set_prop_cycle(plt.cycler('linewidth', [line_width]) +
                          plt.cycler('markersize', [marker_size]))

    plt.rcParams['axes.titlesize'] = title_size
    # Set legend properties with fixed position
    plt.rc('legend', fontsize=legend_size, 
           framealpha=0,
           borderaxespad=0,
           loc='best')
    
    # Enforce figure size
    fig.set_size_inches(total_width_in_inches, height_in_inches, forward=True)
    
    if len(axes) == 1:
        return fig, axes[0]
    return fig, axes