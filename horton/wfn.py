# -*- coding: utf-8 -*-
# Horton is a Density Functional Theory program.
# Copyright (C) 2011-2013 Toon Verstraelen <Toon.Verstraelen@UGent.be>
#
# This file is part of Horton.
#
# Horton is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 3
# of the License, or (at your option) any later version.
#
# Horton is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, see <http://www.gnu.org/licenses/>
#
#--
"""Wavefunction implementations

   The essential part of the wavefunction consists of the expansion coefficients
   in a certain basis.

   Abbreviations used in this module:

   * wfn = wavefunction
   * exp = expansion (orbitals, energies and occupations)
   * dm = density matrix
"""


import numpy as np

from horton.cache import Cache
from horton.log import log
from horton.exceptions import ElectronCountError


__all__ = [
    'WFN', 'ClosedShellWFN', 'OpenShellWFN',
    'AufbauOccModel', 'AufbauSpinOccModel'
]



# TODO: It would be better to use the terms Restricted and Unrestricted, where
# the former only supports close-shell computations for now.


class PropertyHelper(object):
    '''Auxiliary class to set up dm_xxx and exp_xxx attributes of WFN class.'''
    def __init__(self, method, arg):
        self.method = method
        self.arg = arg

    def __get__(self, obj, objtype):
        try:
            return self.method(obj, self.arg)
        except KeyError, e:
            raise AttributeError('The requested attribute (%s) is not available.' % e.args[0])



class WFN(object):
    def __init__(self, occ_model, lf, nbasis, norb=None):
        """
           **Arguments:**

           occ_model
                A model for the occupation numbers to be assigned after
                diagonalization of the Fock matrix.

           lf
                A LinalgFactory instance.

           nbasis
                The number of basis functions.

           **Optional arguments:**

           norb
               the number of orbitals (occupied + virtual). When not given,
               it is set to nbasis.
        """
        self._lf = lf
        self._nbasis = nbasis
        self.occ_model = occ_model
        if norb is None:
            self._norb = nbasis
        else:
            self._norb = norb
        # The cache is used to store different representations of the
        # wavefunction, i.e. as expansion, as density matrix or both.
        self._cache = Cache()
        # Write some screen log
        self._log_init()

    @classmethod
    def from_hdf5(cls, grp, lf):
        # make the wfn object
        from horton.checkpoint import load_hdf5_low
        occ_model = load_hdf5_low(grp['occ_model'], lf)
        result = cls(occ_model, lf, nbasis=grp['nbasis'][()], norb=grp['norb'][()])
        # load stuff into cache
        for spin in 'alpha', 'beta':
            if 'exp_%s' % spin in grp:
                exp = result.init_exp(spin)
                exp.read_from_hdf5(grp['exp_%s' % spin])
            if 'dm_%s' % spin in grp:
                dm = result.init_dm(spin)
                dm.read_from_hdf5(grp['dm_%s' % spin])
        return result

    def to_hdf5(self, grp):
        grp.attrs['class'] = self.__class__.__name__
        grp['nbasis'] = self._nbasis
        grp['norb'] = self._norb
        tmp = grp.create_group('occ_model')
        self.occ_model.to_hdf5(tmp)
        for spin in 'alpha', 'beta':
            if self._cache.has('exp_%s' % spin):
                tmp = grp.create_group('exp_%s' % spin)
                self._cache.load('exp_%s' % spin).to_hdf5(tmp)
            if self._cache.has('dm_%s' % spin):
                tmp = grp.create_group('dm_%s' % spin)
                self._cache.load('dm_%s' % spin).to_hdf5(tmp)

    def _get_nbasis(self):
        '''The number of basis functions.'''
        return self._nbasis

    nbasis = property(_get_nbasis)

    def _get_norb(self):
        '''The number of orbitals in the expansion(s)'''
        return self._norb

    norb = property(_get_norb)

    def _log_init(self):
        '''Write a summary of the wavefunction to the screen logger'''
        if log.do_medium:
            log('Initialized: %s' % self)
            self.occ_model.log()

    def _iter_expansions(self):
        '''Iterate over all expansion in the cache'''
        for spin in 'alpha', 'beta':
            if self._cache.has('exp_%s' % spin):
                yield self._cache.load('exp_%s' % spin)

    def _iter_density_matrices(self):
        '''Iterate over all density matrices in the cache'''
        for select in 'alpha', 'beta', 'full', 'spin':
            if self._cache.has('dm_%s' % select):
                yield self._cache.load('dm_%s' % select)

    def _assign_dm_full(self, dm):
        raise NotImplementedError

    def _assign_dm_spin(self, dm):
        raise NotImplementedError

    def invalidate(self):
        '''Must be called when the wavefunction is outdated'''
        self._cache.invalidate_all()

    def init_exp(self, spin):
        if spin not in ['alpha', 'beta']:
            raise ValueError('The select argument must be alpha or beta')
        exp, new = self._cache.load('exp_%s' % spin, alloc=(self._lf, 'expansion', self._nbasis, self._norb))
        if not new:
            raise RuntimeError('The expansion exp_%s already exists. Call wfn.invalidate prior to updating the wfn.' % spin)
        return exp

    def init_dm(self, select):
        if select not in ['alpha', 'beta', 'full', 'spin']:
            raise ValueError('The select argument must be one of alpha, beta, full or spin.')
        dm, new = self._cache.load('dm_%s' % select, alloc=(self._lf, 'one_body', self.nbasis))
        if not new:
            raise RuntimeError('The density matrix dm_%s already exists. Call wfn.invalidate prior to updating the wfn.' % select)
        return dm

    def update_dm(self, select, dm=None):
        """Derive the density matrix from the expansion(s) and store in cache

           **Optional arguments:**

           select
                'alpha', 'beta', 'full' or 'spin'.
        """
        cached_dm = self.init_dm(select)
        if dm is None:
            if select == 'alpha':
                exp_alpha = self._cache.load('exp_alpha')
                exp_alpha.compute_density_matrix(cached_dm)
            elif select == 'beta':
                exp_beta = self._cache.load('exp_beta')
                exp_beta.compute_density_matrix(cached_dm)
            elif select == 'full':
                self._assign_dm_full(cached_dm)
            elif select == 'spin':
                self._assign_dm_spin(cached_dm)
        else:
            cached_dm.assign(dm)
        return cached_dm

    def get_dm(self, select):
        '''Get a density matrix. If not available, it will be created (if possible)

           select
                'alpha', 'beta', 'full' or 'spin'.
        '''
        if not self._cache.has('dm_%s' % select):
            self.update_dm(select)
        return self._cache.load('dm_%s' % select)

    def get_exp(self, spin):
        '''Return an expansion of the wavefunction, if available.

           **Optional arguments:**

           select
                the spin component: 'alpha' or 'beta'.
        '''
        return self._cache.load('exp_%s' % spin)

    dm_alpha = PropertyHelper(get_dm, 'alpha')
    dm_beta = PropertyHelper(get_dm, 'beta')
    dm_full = PropertyHelper(get_dm, 'full')
    dm_spin = PropertyHelper(get_dm, 'spin')
    exp_alpha = PropertyHelper(get_exp, 'alpha')
    exp_beta = PropertyHelper(get_exp, 'beta')

    def apply_basis_permutation(self, permutation):
        """Reorder the expansion coefficients and the density matrices"""
        for exp in self._iter_expansions():
            exp.apply_basis_permutation(permutation)
        for dm in self._iter_density_matrices():
            dm.apply_basis_permutation(permutation)

    def apply_basis_signs(self, signs):
        """Fix the signs of the expansion coefficients and the density matrices"""
        for exp in self._iter_expansions():
            exp.apply_basis_signs(signs)
        for dm in self._iter_density_matrices():
            dm.apply_basis_signs(signs)

    def check_normalization(self, olp, eps=1e-4):
        '''Run an internal test to see if the orbitals are normalized

           **Arguments:**

           olp
                The overlap one_body operators

           **Optional arguments:**

           eps
                The allowed deviation from unity, very loose by default.
        '''
        for exp in self._iter_expansions():
            exp.check_normalization(olp, eps)


class ClosedShellWFN(WFN):
    closed_shell = True # TODO: ugly

    def _assign_dm_full(self, dm):
        dm.assign(self.dm_alpha)
        dm.iscale(2)

    def _assign_dm_spin(self, dm):
        dm.reset()

    def update_exp(self, fock_alpha, overlap, dm_alpha=None):
        '''Update the expansion based on the given fock matrix

           **Arguments:**

           fock_alpha
                A DenseOneBody object with the Fock operator

           overlap
                A DenseOneBody object with the overlap operator

           ***Optional arguments:**

           dm_alpha
                A DenseOneBody object with the density matrix

        '''
        # Load the orbital expansion of the alpha density.
        exp_alpha = self.init_exp('alpha')
        if dm_alpha is None:
            # Diagonalize the Fock matrix and
            # use simple rules to derive the occupations
            exp_alpha.derive_from_fock_matrix(fock_alpha, overlap)
            self.occ_model.assign(exp_alpha)
        else:
            # Diagonalize the Fock matrix and
            # use the density matrix to derive the occupations
            cached_dm_alpha = self.init_dm('alpha')
            cached_dm_alpha.assign(dm_alpha)
            exp_alpha.derive_from_density_and_fock_matrix(dm_alpha, fock_alpha, overlap)
        return exp_alpha

    def _get_nel(self):
        return self.nep*2

    nel = property(_get_nel)

    def _get_nep(self):
        if self._cache.has('exp_alpha'):
            return self._cache.load('exp_alpha').occupations.sum()
        else:
            raise NotImplementedError

    nep = property(_get_nep)

    def _get_mult(self):
        return 1.0

    mult = property(_get_mult)

    def get_spin(self):
        '''Returns the expectation values of the projecte and squared spin'''
        return 0.0, 0.0



class OpenShellWFN(WFN):
    closed_shell = False # TODO: ugly

    def _assign_dm_full(self, dm):
        dm.assign(self.dm_alpha)
        dm.iadd(self.dm_beta)

    def _assign_dm_spin(self, dm):
        dm.assign(self.dm_alpha)
        dm.iadd(self.dm_beta, factor=-1)

    def update_exp(self, fock_alpha, fock_beta, overlap, dm_alpha=None, dm_beta=None):
        '''Update the expansion based on the given fock matrix

           **Arguments:**

           fock_alpha, fock_beta
                DenseOneBody objects with the Fock operators

           overlap
                A DenseOneBody object with the overlap operator

           ***Optional arguments:**

           dm_alpha, dm_beta
                DenseOneBody objects with the density matrices
        '''
        if ((dm_alpha is None) ^ (dm_beta is None)):
            raise ValueError('Either no or both density matrices must be given')

        # Load the orbital expansions.
        exp_alpha = self.init_exp('alpha')
        exp_beta = self.init_exp('beta')
        if dm_alpha is None:
            # Diagonalize the Fock matrix and
            # use simple rules to derive the occupations
            exp_alpha.derive_from_fock_matrix(fock_alpha, overlap)
            if fock_alpha is fock_beta:
                # take a shortcut
                exp_beta.assign(exp_alpha)
            else:
                exp_beta.derive_from_fock_matrix(fock_beta, overlap)
            self.occ_model.assign(exp_alpha, exp_beta)
        else:
            # Diagonalize the Fock matrix and
            # use the density matrix to derive the occupations
            #   alpha
            cached_dm_alpha = self.init_dm('alpha')
            cached_dm_alpha.assign(dm_alpha)
            exp_alpha.derive_from_density_and_fock_matrix(dm_alpha, fock_alpha, overlap)
            #   beta
            cached_dm_beta = self.init_dm('beta')
            cached_dm_beta.assign(dm_beta)
            exp_beta.derive_from_density_and_fock_matrix(dm_beta, fock_beta, overlap)
        return exp_alpha, exp_beta

    def _get_nel(self):
        return self.nalpha + self.nbeta

    nel = property(_get_nel)

    def _get_nalpha(self):
        if self._cache.has('exp_alpha'):
            return self._cache.load('exp_alpha').occupations.sum()
        else:
            raise NotImplementedError

    nalpha = property(_get_nalpha)

    def _get_nbeta(self):
        if self._cache.has('exp_beta'):
            return self._cache.load('exp_beta').occupations.sum()
        else:
            raise NotImplementedError

    nbeta = property(_get_nbeta)

    def _get_mult(self):
        return 1 + abs(self.nalpha - self.nbeta)

    mult = property(_get_mult)

    def get_spin(self, olp):
        '''Returns the expectation values of the projected and squared spin

           **Arguments:**

           olp
                The overlap matrix
        '''
        nbeta = self.nbeta
        sz = (self.nalpha - nbeta)/2

        # The correction due to the mismatch in overlap between alpha and beta
        # orbitals.
        correction = 0.0
        nfn_alpha = self.exp_alpha.nfn
        nfn_beta = self.exp_beta.nfn
        occupations_alpha = self.exp_alpha.occupations
        occupations_beta = self.exp_beta.occupations
        coeffs_alpha = self.exp_alpha.coeffs
        coeffs_beta = self.exp_beta.coeffs
        for ialpha in xrange(nfn_alpha):
            if occupations_alpha[ialpha] == 0.0:
                continue
            for ibeta in xrange(nfn_beta):
                if occupations_beta[ibeta] == 0.0:
                    continue
                correction += olp.dot(coeffs_alpha[:,ialpha], coeffs_beta[:,ibeta])**2

        ssq = sz*(sz+1) + nbeta - correction
        return sz, ssq


class AufbauOccModel(object):
    def __init__(self, nalpha, nbeta=None):
        if nbeta is None:
            nbeta = nalpha

        if nalpha < 0 or nbeta < 0:
            raise ElectronCountError('Negative number of electrons is not allowed.')
        if nalpha == 0 and nbeta == 0:
            raise ElectronCountError('At least one alpha or beta electron is required.')

        self.nalpha = nalpha
        self.nbeta = nbeta

    @classmethod
    def from_hdf5(cls, grp, lf):
        return cls(grp['nalpha'][()], grp['nbeta'][()])

    def to_hdf5(self, grp):
        grp.attrs['class'] = self.__class__.__name__
        grp['nalpha'] = self.nalpha
        grp['nbeta'] = self.nbeta

    def assign(self, exp_alpha, exp_beta=None):
        '''Assign occupation numbers to the expansion objects

           **Arguments:**

           exp_alpha
                An alpha DenseExpansion object

           **Optional arguments:**

           exp_beta
                A beta DenseExpansion object
        '''
        exps = [(exp_alpha, self.nalpha)]
        if exp_beta is not None:
            exps.append((exp_beta, self.nbeta))
        for exp, nocc in exps:
            nfn = len(exp.energies)
            assert nfn == len(exp.occupations)
            if nfn < nocc:
                raise ElectronCountError('The number of orbitals must not be lower than the number of alpha or beta electrons.')
            assert (exp.energies[1:] >= exp.energies[:-1]).all()
            exp.occupations[:nocc] = 1.0
            exp.occupations[nocc:] = 0.0

    def log(self):
        log('Occupation model: %s' % self)
        log.deflist([('nalpha', self.nalpha), ('nbeta', self.nbeta)])


class AufbauSpinOccModel(object):
    '''This Aufbau model only applies to unrestricted wavefunctions'''
    def __init__(self, nel):
        if nel <= 0:
            raise ElectronCountError('The number of electron must be positive.')

        self.nel = nel

    @classmethod
    def from_hdf5(cls, grp, lf):
        return cls(grp['nel'][()])

    def to_hdf5(self, grp):
        grp.attrs['class'] = self.__class__.__name__
        grp['nel'] = self.nel

    def assign(self, exp_alpha, exp_beta):
        '''Assign occupation numbers to the expansion objects

           **Arguments:**

           exp_alpha, exp_beta
                Alpha and beta DenseExpansion object
        '''
        nel = self.nel
        ialpha = 0
        ibeta = 0
        while nel > 0:
            if exp_alpha.energies[ialpha] <= exp_beta.energies[ibeta]:
                exp_alpha.occupations[ialpha] = 1.0
                ialpha += 1
            else:
                exp_beta.occupations[ibeta] = 1.0
                ibeta += 1
            nel -= 1
        #print 'AufbauSpinOccModel', ialpha, ibeta

    def log(self):
        log('Occupation model: %s' % self)
        log.deflist([('nel', self.nbeta)])
