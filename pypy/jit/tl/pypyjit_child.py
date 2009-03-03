from pypy.conftest import option
from pypy.rpython.lltypesystem import lltype
from pypy.jit.metainterp import warmspot
from pypy.module.pypyjit.portal import PyPyJitPolicy


# Current output: http://paste.pocoo.org/show/105784/
#
# Some optimizations missing:
#
#   - improve the optimization: e.g. ooisnull followed by oononnull
#     on the same variable
#
#   - reintroduce some delayed list optimizations to remove this
#     mess of push/pop on the stack
#
#   - remove the useless 'ec' argument (p1 and p115 in the trace)
#
#   - figure out who calls W_TypeObject.is_heaptype(), leading to
#     the "int_and 512" (lines 48, 147, 154)
#
#   - the guards have very long 'liveboxes' lists containing mostly
#     Consts -- make sure that these Consts are not stored, or else
#     remove them entirely
#
#   - dead operation removal: e.g. unused 'getfield_gc' (line 158)


def run_child(glob, loc):
    import sys, pdb
    interp = loc['interp']
    graph = loc['graph']
    interp.malloc_check = False

    def returns_null(T, *args, **kwds):
        return lltype.nullptr(T)
    interp.heap.malloc_nonmovable = returns_null     # XXX

    print 'warmspot.jittify_and_run() started...'
    policy = PyPyJitPolicy(interp.typer.annotator.translator)
    option.view = True
    try:
        warmspot.jittify_and_run(interp, graph, [], policy=policy,
                                 listops=True)
    except Exception, e:
        print '%s: %s' % (e.__class__, e)
        pdb.post_mortem(sys.exc_info()[2])
