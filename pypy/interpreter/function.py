"""
Function objects.

In PyPy there is no difference between built-in and user-defined function
objects; the difference lies in the code object found in their func_code
attribute.
"""

from error import OperationError

class Function(object):
    """A function is a code object captured with some environment:
    an object space, a dictionary of globals, default arguments,
    and an arbitrary 'closure' passed to the code object."""
    
    def __init__(self, space, code, w_globals, w_defs=None, closure=None):
        self.space     = space
        self.func_code = code       # Code instance
        self.w_globals = w_globals  # the globals dictionary
        self.closure   = closure    # normally, list of Cell instances or None
        if w_defs is None:
            self.defs_w = []
        else:
            self.defs_w = space.unpackiterable(w_defs)  # list of w_default's

    def call(self, w_args, w_kwds=None):
        scope_w = self.parse_args(w_args, w_kwds)
        frame = self.func_code.create_frame(self.space, self.w_globals,
                                            self.closure)
        frame.setfastscope(scope_w)
        return frame.run()

    def parse_args(self, w_args, w_kwds=None):
        """ parse args and kwargs to initialize the frame.
        """
        space = self.space
        signature = self.func_code.signature()
        argnames, varargname, kwargname = signature
        #
        #   w_args = wrapped sequence of the normal actual parameters
        #   args_w = the same, as a list of wrapped actual parameters
        #   w_kwds = wrapped dictionary of keyword parameters or a real None
        #   argnames = list of formal parameter names
        #   scope_w = resulting list of wrapped values
        #
        # We try to give error messages following CPython's, which are
        # very informative.
        #
        if w_kwds is None:
            w_kwargs = space.newdict([])
        else:
            w_kwargs = space.call_method(w_kwds, "copy")

        co_argcount = len(argnames) # expected formal arguments, without */**

        # put as many positional input arguments into place as available
        args_w = space.unpacktuple(w_args)
        scope_w = args_w[:co_argcount]
        input_argcount = len(scope_w)

        # check that no keyword argument conflicts with these
        for name in argnames[:input_argcount]:
            w_name = space.wrap(name)
            if space.is_true(space.contains(w_kwargs, w_name)):
                self.raise_argerr_multiple_values(name)

        if input_argcount < co_argcount:
            # not enough args, fill in kwargs or defaults if exists
            def_first = co_argcount - len(self.defs_w)
            for i in range(input_argcount, co_argcount):
                w_name = space.wrap(argnames[i])
                if space.is_true(space.contains(w_kwargs, w_name)):
                    scope_w.append(space.getitem(w_kwargs, w_name))
                    space.delitem(w_kwargs, w_name)
                elif i >= def_first:
                    scope_w.append(self.defs_w[i-def_first])
                else:
                    self.raise_argerr(w_args, w_kwds, False)
                    
        # collect extra positional arguments into the *vararg
        if varargname is not None:
            scope_w.append(space.newtuple(args_w[co_argcount:]))
        elif len(args_w) > co_argcount:
            self.raise_argerr(w_args, w_kwds, True)

        # collect extra keyword arguments into the **kwarg
        if kwargname is not None:
            # XXX this doesn't check that the keys of kwargs are strings
            scope_w.append(w_kwargs)
        elif space.is_true(w_kwargs):
            self.raise_unknown_kwds(w_kwds)
        return scope_w

    # helper functions to build error message for the above

    def raise_argerr(self, w_args, w_kwds, too_many):
        argnames, varargname, kwargname = self.func_code.signature()
        nargs = self.space.unwrap(self.space.len(w_args))
        n = len(argnames)
        if n == 0:
            if kwargname is not None:
                msg2 = "non-keyword "
            else:
                msg2 = ""
                nargs += self.space.unwrap(self.space.len(w_kwds))
            msg = "%s() takes no %sargument (%d given)" % (
                self.func_code.co_name,
                msg2,
                nargs)
        else:
            defcount = len(self.defs_w)
            if defcount == 0:
                msg1 = "exactly"
            elif too_many:
                msg1 = "at most"
            else:
                msg1 = "at least"
                n -= defcount
            if kwargname is not None:
                msg2 = "non-keyword "
            else:
                msg2 = ""
            if n == 1:
                plural = ""
            else:
                plural = "s"
            msg = "%s() takes %s %d %sargument%s (%d given)" % (
                self.func_code.co_name,
                msg1,
                n,
                msg2,
                plural,
                nargs)
        raise OperationError(self.space.w_TypeError, msg)

    def raise_argerr_multiple_values(self, argname):
        msg = "%s() got multiple values for keyword argument %s" % (
            self.func_code.co_name,
            argname)
        raise OperationError(self.space.w_TypeError, msg)

    def raise_argerr_unknown_kwds(self, w_kwds):
        nkwds = self.space.unwrap(self.space.len(w_kwds))
        if nkwds == 1:
            w_iter = self.space.iter(w_kwds)
            w_key = self.space.next(w_iter)
            msg = "%s() got an unexpected keyword argument '%s'" % (
                self.func_code.co_name,
                self.space.unwrap(w_key))
        else:
            msg = "%s() got %d unexpected keyword arguments" % (
                self.func_code.co_name,
                nkwds)
        raise OperationError(self.space.w_TypeError, msg)


    def __get__(self, inst, cls=None):
        # for TrivialObjSpace only !!!
        # use the mecanisms of gateway.py otherwise
        import sys, new
        assert 'pypy.objspace.trivial' in sys.modules, (
            "don't try to __get__() Function instances out of classes")
        self.__name__ = self.func_code.co_name
        return new.instancemethod(self, inst, cls)

    def __call__(self, *args, **kwds):
        # for TrivialObjSpace only !!!
        # use the mecanisms of gateway.py otherwise
        import sys, new
        assert 'pypy.objspace.trivial' in sys.modules, (
            "don't try to __call__() Function instances directly")
        return self.call(args, kwds)
