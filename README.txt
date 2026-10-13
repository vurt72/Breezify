Breezify by Vurt
================
Breezify gives Morrowind trees realistic wind animation, made for OpenMW.

  * The upper, thinner part of the trunk bends in the wind while the base stays planted.
  * The crown moves in slow gusts, with swaying, bobbing, twisting and quick leaf flutter.
  * Every leaf cluster moves a little on its own but stays attached to its branch, so the tree
    never moves as one rigid block.
  * The motion loops every 120 seconds and is built so the repeat isn't noticeable.

It works on static trees and on trees with the old-style "rotating leaves" animation, which it
replaces. It also makes an optimized version of each tree for distant views.

You don't need to install anything. Breezify includes its own private copy of Python, which only
runs inside this folder and doesn't touch the rest of your PC.


HOW TO USE
----------
1. Drag one or more tree .nif files, or a whole folder of them, onto Breezify.bat.
   (Use Breezify_mgexe.bat instead if you use MGE XE, see below.)
2. Breezify writes the results to a "wind_output" folder next to the files you dropped.
   Your original files are never changed.
3. Copy the files from wind_output into your Data Files\meshes folder, in the same subfolder
   the trees came from (usually meshes\f).

That's it. A window shows what was done with each tree; press a key to close it.

Vanilla trees are packed inside Morrowind.bsa and Bloodmoon.bsa. Extract them first (for
example with BSA Browser), run Breezify on the extracted files, then put the results in
Data Files\meshes.


WHICH .BAT SHOULD I USE?
------------------------
  Breezify.bat         For OpenMW. Distant trees keep two-sided leaves, which OpenMW respects.

  Breezify_mgexe.bat   For MGE XE distant land. MGE XE ignores two-sided leaves at a distance,
                       so distant trees can look thin from behind. This version gives the
                       distant leaves real back faces (copied and flipped) so they stay full.
                       The near, animated trees are identical in both versions.


WHAT YOU GET FOR EACH TREE
--------------------------
  <name>.nif        The wind-animated tree.

  <name>_dist.nif   An optimized, still (non-animated) version that the game uses for distant
                    trees:
                      - all parts with the same material merged into one, so far fewer draw
                        calls (a vanilla tree goes from about 50 to 2-6)
                      - trunk and branches reduced to about 30% of their triangles, dense
                        foliage to about 50%, by removing points only, so the bark texture
                        never gets stretched
                      - no collision and no animation
                    If a tree already comes with its own _dist.nif, that one is kept and
                    Breezify doesn't make a new one.


GOOD TO KNOW
------------
  * Made and tested for OpenMW 0.51. The original Morrowind engine and MGE XE are untested.
    The MGE XE back-face method is the one Vurt has always used for distant trees.
  * Breezify finds leaves by their transparency (alpha). Trees without see-through leaves,
    such as bare trees and stumps, are skipped and left alone.
  * Collision is kept exactly as it was.
  * Each animated tree file is about 650-900 KB, mostly the animation itself. In testing,
    10+ animated trees on screen made no difference to the frame rate.
  * OpenMW plays every copy of the same tree model in sync, so two identical trees next to
    each other sway together (OpenMW issue #6455). Different tree models move differently.
  * Running Breezify twice on the same tree does nothing: an already Breezified tree is skipped.
  * If a tree is skipped with "block type ... is not supported", that mesh uses something
    Breezify can't read yet. Nothing is written for it.


OPTIONS (for advanced users)
----------------------------
Breezify can also be run from a command prompt opened in this folder:

    python\python.exe breezify.py <files or folders> [options]

  --sway 0.012      trunk sway, as a fraction of the tree's height
  --branch 0.010    branch movement
  --flutter 0.004   fast leaf flutter
                    For example, --sway 0.02 --branch 0.016 gives a windier, coastal look.
  --seed N          a different motion pattern (by default each tree model gets its own)
  -o FOLDER         write the results to another folder
  --mge             distant leaves with back faces (this is what Breezify_mgexe.bat does)
  --no-dist         don't make _dist files
  --no-merge        don't merge same-material parts of the animated tree
  --force           also animate trees that have no see-through leaves


INCLUDED SOFTWARE
-----------------
  python\         The official Windows embeddable Python 3.13.16 from python.org
                  (PSF license, see python\LICENSE.txt)
  numpy 2.5.4     BSD license, see python\Lib\site-packages\numpy-2.5.4.dist-info\
                  (its self-tests and developer tools were removed to save space)
  meshopt.dll     meshoptimizer v0.25 by Arseny Kapoulkine (MIT license, see
                  meshoptimizer_LICENSE.md), compiled from the official source for
                  64-bit Windows. Used to make the optimized _dist trees.

Requires 64-bit Windows 10 or 11.


CREDITS
-------
Breezify by Vurt. The wind system was first made for Vurt's Animated Flora Overhaul
(Fallout 3) and ported to Morrowind.
