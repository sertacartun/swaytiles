# Layouts

Every layout is fed the same list: the windows of the workspace in order,
the first one being the master. Opening a window adds it at the end,
closing one lets the rest move up, and moves and swaps change the order.
The layout turns that list into a tree of sway containers.

<table>
<tr>
<td width="50%" valign="top">
<h3><code>master</code></h3>
<img src="gifs/master.gif" alt="master layout">
<p>The first window, the master, takes the left half. The others stack on the right in the order they opened. A new window joins the bottom of the stack, and when the master closes the top of the stack takes its place.</p>
</td>
<td width="50%" valign="top">
<h3><code>master-right</code></h3>
<img src="gifs/master-right.gif" alt="master-right layout">
<p>The same as <code>master</code>, mirrored: the master on the right and the stack on the left.</p>
</td>
</tr>
<tr>
<td width="50%" valign="top">
<h3><code>wide</code></h3>
<img src="gifs/wide.gif" alt="wide layout">
<p>The master spans the top, and the stack is a row of windows below it. Good for a wide monitor or for reading long lines.</p>
</td>
<td width="50%" valign="top">
<h3><code>centered</code></h3>
<img src="gifs/centered.gif" alt="centered layout">
<p>The master sits in the middle, and the stack alternates between the two sides: windows 2 and 4 on the right, 3 and 5 on the left. With two windows it is <code>master</code>.</p>
</td>
</tr>
<tr>
<td width="50%" valign="top">
<h3><code>tabbed-master</code></h3>
<img src="gifs/tabbed-master.gif" alt="tabbed-master layout">
<p>The master on the left, and the stack as tabs on the right, so one stack window is visible at a time at full height. Good for small screens.</p>
</td>
<td width="50%" valign="top">
<h3><code>stacked-master</code></h3>
<img src="gifs/stacked-master.gif" alt="stacked-master layout">
<p>The same as <code>tabbed-master</code>, with the stack as a column of stacked titles.</p>
</td>
</tr>
<tr>
<td width="50%" valign="top">
<h3><code>dwindle</code></h3>
<img src="gifs/dwindle.gif" alt="dwindle layout">
<p>Each window takes half of the space left over, splitting right, then down, then right again, so later windows get smaller.</p>
</td>
<td width="50%" valign="top">
<h3><code>spiral</code></h3>
<img src="gifs/spiral.gif" alt="spiral layout">
<p>Like <code>dwindle</code>, but the splits turn clockwise (right, down, left, up), so the windows spiral inwards.</p>
</td>
</tr>
<tr>
<td width="50%" valign="top">
<h3><code>grid</code></h3>
<img src="gifs/grid.gif" alt="grid layout">
<p>As many columns as the square root of the window count, rounded up, filled row by row. A last row with fewer windows makes them wider.</p>
</td>
<td width="50%" valign="top">
<h3><code>float</code></h3>
<img src="gifs/float.gif" alt="float layout">
<p>Every window floats at 60% of the output, cascaded around its centre, and a new window never covers another one exactly. Tile a window by hand and it stays tiled.</p>
</td>
</tr>
<tr>
<td width="50%" valign="top">
<h3><code>tabbed</code></h3>
<img src="gifs/tabbed.gif" alt="tabbed layout">
<p>Every window is a tab and one is visible. Moves reorder the tabs.</p>
</td>
<td width="50%" valign="top">
<h3><code>stacking</code></h3>
<img src="gifs/stacking.gif" alt="stacking layout">
<p>Every window is a stacked title and one is visible. Moves reorder the titles.</p>
</td>
</tr>
</table>


`default` is no layout: the workspace is left to sway, as if swaytiles
were not running. It is the layout until you pick one.

Each workspace has its own layout, remembered across restarts; a new
workspace starts with the last layout picked. The menu marks the current
one, and says when it is paused.
