"""Files of entries in, and the owner's own entries out (M7.22).

``csvutil`` reads and writes CSV text and the cells in it, ``preview`` turns the rows a format has
read into a preview of what an import would do to a tab, and ``generic`` is the format the platform
writes itself. Like ``core/``, nothing here performs I/O: the text arrives as an argument, what
the tab already holds and how symbols and accounts resolve arrive in a ``Context``, and the answer
leaves as a return value. The store, the batches and the undo are the service's (PB9).
"""
