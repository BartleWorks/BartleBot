"""What a second product by this publisher needs, and nothing this one does.

BartleBuddy (a live questing assistant) and BartleBuilder (a build simulator) are
planned beside BartleBot, and all three want the same accounts, the same launch
profiles and the same stored passwords. Asking a user to enter their accounts twice
is not a thing to ship, so the parts that are about the *account* rather than about
this application live here.

The constraint that makes that possible is a rule about imports: **nothing in this
subpackage may import from ``bartlebot.gui`` or ``bartlebot.bot``.** It is what keeps
the layer extractable — into a fourth repo consumed as a pinned dependency, or into an
installable package — without dragging a bot canvas and an FSM along with it.
``test_shared_layer_imports_nothing_product_specific`` is the guard.

The seam that decides most cases: a *record* is shared, a *judgement about it made
against this app's window* is not. A launch profile's resolution override lives here;
``screen_fit``'s verdict on whether it fits, which measures this app's sidebars and bot
canvas, stays in ``gui/``. Same shape for account colors: the slot arithmetic is here
in ``account_slots``, resolving a slot against the live theme is ``gui/account_accent``.
"""
