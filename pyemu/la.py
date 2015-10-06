from __future__ import print_function, division
import os
import copy
from datetime import datetime
import numpy as np
#import mat_handler as mhand
#import pst_handler as phand
from pyemu.mat.mat_handler import matrix, jco, cov
from pyemu.pst.pst_handler import pst


class logger(object):
    """ a basic class for logging events during the linear analysis calculations
        if filename is passed, then an file handle is opened
    Args:
        filename (bool or string): if string, it is the log file to write
            if a bool, then log is written to the screen
        echo (bool): a flag to force screen output
    Attributes:
        items (dict) : tracks when something is started.  If a log entry is
            not in items, then it is treated as a new entry with the string
            being the key and the datetime as the value.  If a log entry is
            in items, then the end time and delta time are written and
            the item is popped from the keys

    """
    def __init__(self,filename, echo=False):
        self.items = {}
        self.echo = bool(echo)
        if filename == True:
            self.echo = True
            self.filename = None
        elif filename:
            self.f = open(filename, 'w', 0) #unbuffered
            self.t = datetime.now()
            self.log("opening " + str(filename) + " for logging")
        else:
            self.filename = None


    def log(self,phrase):
        """log something that happened
        Args:
            phrase (str) : the thing that happened
        Returns:
            None
        Raises:
            None
        """
        pass
        t = datetime.now()
        if phrase in self.items.keys():
            s = str(t) + ' finished: ' + str(phrase) + " took: " + \
                str(t - self.items[phrase]) + '\n'
            if self.echo:
                print(s,)
            if self.filename:
                self.f.write(s)
            self.items.pop(phrase)
        else:
            s = str(t) + ' starting: ' + str(phrase) + '\n'
            if self.echo:
                print(s,)
            if self.filename:
                self.f.write(s)
            self.items[phrase] = copy.deepcopy(t)

    def warn(self,message):
        """write a warning to the log file
        Args:
            message (str) : the warning text
        Returns:
            None
        Raises:
            None
        """
        s = str(datetime.now()) + " WARNING: " + message + '\n'
        if self.echo:
            print(s,)
        if self.filename:
            self.f.write(s)


class linear_analysis(object):
    """ the super class for linear analysis.  Can be used for prior analyses
        only.  The derived types (schur and errvar) are for posterior analyses
        this class tries hard to not load items until they are needed
        all arguments are optional

        Args:
            jco ([enumerable of] [string,ndarray,matrix objects]) : jacobian
            pst (pst object) : the pest control file object
            parcov ([enumerable of] [string,ndarray,matrix objects]) :
                parameter covariance matrix
            obscov ([enumerable of] [string,ndarray,matrix objects]):
                observation noise covariance matrix
            predictions ([enumerable of] [string,ndarray,matrix objects]) :
                prediction sensitivity vectors
            ref_var (float) : reference variance
            verbose (either bool or string) : controls log file / screen output
        Attributes:
            too many to list...just figure it out
        Notes:
            the class makes heavy use of property decorator to encapsulate
            private attributes
    """
    def __init__(self, jco=None, pst=None, parcov=None, obscov=None,
                 predictions=None, ref_var=1.0, verbose=False,
                 resfile=False, forecasts=None,**kwargs):
        self.logger = logger(verbose)
        self.log = self.logger.log
        self.jco_arg = jco
        if jco is None:
            self.__jco = jco()
        if pst is None:
            if isinstance(jco, str):
                pst_case = jco.replace(".jco", ".pst").replace(".jcb",".pst")
                if os.path.exists(pst_case):
                    pst = pst_case
        self.pst_arg = pst
        if parcov is None and pst is not None:
            parcov = pst
        self.parcov_arg = parcov
        if obscov is None and pst is not None:
            obscov = pst
        self.obscov_arg = obscov
        self.ref_var = ref_var
        if forecasts is not None and predictions is not None:
            raise Exception("can't pass both forecasts and predictions")
        if forecasts is not None:
            predictions = forecasts
        self.prediction_arg = predictions

        #private attributes - access is through @decorated functions
        self.__pst = None
        self.__parcov = None
        self.__obscov = None
        self.__predictions = None
        self.__qhalf = None
        self.__qhalfx = None
        self.__xtqx = None
        self.__fehalf = None
        self.__prior_prediction = None

        self.log("pre-loading base components")
        if jco is not None:
            self.__load_jco()
        if pst is not None:
            self.__load_pst()
        if parcov is not None:
            self.__load_parcov()
        if obscov is not None:
            self.__load_obscov()

        if predictions is not None:
            self.__load_predictions()
        self.log("pre-loading base components")
        if len(kwargs.keys()) > 0:
            self.logger.warn("unused kwargs in type " +
                             str(self.__class__.__name__) +
                             " : " + str(kwargs))
            raise Exception("unused kwargs" +
                             " : " + str(kwargs))
        # automatically do some things that should be done
        self.log("dropping prior information")
        pi = None
        try:
            pi = self.pst.prior_information
        except:
            self.logger.warn("unable to access self.pst: can't tell if " +
                             " any prior information needs to be dropped.")
        if pi is not None:
            self.drop_prior_information()
        self.log("dropping prior information")


        if resfile != False:
            self.log("scaling obscov by residual phi components")
            try:
                self.adjust_obscov_resfile(resfile=resfile)
            except:
                self.logger.warn("unable to a find a residuals file for " +\
                                " scaling obscov")
                self.resfile = None
                self.res = None
            self.log("scaling obscov by residual phi components")

    def __fromfile(self, filename):
        """a private method to deduce and load a filename into a matrix object

            Args:
                filename (str) : the name of the file
            Returns:
                mat (or cov) object
            Raises:
                Exception if filename extension is not in [jco,mat,vec,cov,unc]

        """
        ext = filename.split('.')[-1].lower()
        if ext in ["jco", "jcb"]:
            self.log("loading jco: "+filename)
            m = jco()
            m.from_binary(filename)
            self.log("loading jco: "+filename)
        elif ext in ["mat","vec"]:
            self.log("loading ascii: "+filename)
            m = matrix()
            m.from_ascii(filename)
            self.log("loading ascii: "+filename)
        elif ext in ["cov"]:
            self.log("loading cov: "+filename)
            m = cov()
            m.from_ascii(filename)
            self.log("loading cov: "+filename)
        elif ext in["unc"]:
            self.log("loading unc: "+filename)
            m = cov()
            m.from_uncfile(filename)
            self.log("loading unc: "+filename)
        else:
            raise Exception("linear_analysis.__fromfile(): unrecognized" +
                            " filename extension:" + str(ext))
        return m


    def __load_pst(self):
        """private: set the pst attribute
        Args:
            None
        Returns:
            None
        Raises:
            Exception from instantiating a pst object
        """
        if self.pst_arg is None:
            return None
        if isinstance(self.pst_arg, pst):
            self.__pst = self.pst_arg
            return self.pst
        else:
            try:
                self.log("loading pst: " + str(self.pst_arg))
                self.__pst = pst(self.pst_arg)
                self.log("loading pst: " + str(self.pst_arg))
                return self.pst
            except Exception as e:
                raise Exception("linear_analysis.__load_pst(): error loading"+\
                                " pest control from argument: " +
                                str(self.pst_arg) + '\n->' + str(e))


    def __load_jco(self):
        """private :set the jco attribute from a file or a matrix object
        Args:
            None
        Returns:
            None
        Raises:
            Exception if the jco_arg is not a matrix object or str
        """
        if self.jco_arg is None:
            return None
            #raise Exception("linear_analysis.__load_jco(): jco_arg is None")
        if isinstance(self.jco_arg, matrix):
            self.__jco = self.jco_arg
        elif isinstance(self.jco_arg, str):
            self.__jco = self.__fromfile(self.jco_arg)
        else:
            raise Exception("linear_analysis.__load_jco(): jco_arg must " +
                            "be a matrix object or a file name: " +
                            str(self.jco_arg))


    def __load_parcov(self):
        """private: set the parcov attribute from:
                a pest control file (parameter bounds)
                a pst object
                a matrix object
                an uncert file
                an ascii matrix file
        Args:
            None
        Returns:
            None
        Raises:
            Exception is the parcov_arg is not a matrix object or string
        """
        # if the parcov arg was not passed but the pst arg was,
        # reset and use parbounds to build parcov
        if not self.parcov_arg:
            if self.pst_arg:
                self.parcov_arg = self.pst_arg
            else:
                raise Exception("linear_analysis.__load_parcov(): " +
                                "parcov_arg is None")
        if isinstance(self.parcov_arg, matrix):
            self.__parcov = self.parcov_arg
            return
        if isinstance(self.parcov_arg, np.ndarray):
            # if the passed array is a vector,
            # then assume it is the diagonal of the parcov matrix
            if len(self.parcov_arg.shape) == 1:
                assert self.parcov_arg.shape[0] == self.jco.shape[1]
                isdiagonal = True
            else:
                assert self.parcov_arg.shape[0] == self.jco.shape[1]
                assert self.parcov_arg.shape[1] == self.jco.shape[1]
                isdiagonal = False
            self.logger.warn("linear_analysis.__load_parcov(): " +
                             "instantiating parcov from ndarray, can't " +
                             "verify parameters alignment with jco")
            self.__parcov = matrix(x=self.parcov_arg,
                                         isdiagonal=isdiagaonal,
                                         row_names=self.jco.col_names,
                                         col_names=self.jco.col_names)
        self.log("loading parcov")
        if isinstance(self.parcov_arg,str):
            # if the arg is a string ending with "pst"
            # then load parcov from parbounds
            if self.parcov_arg.lower().endswith(".pst"):
                self.__parcov = cov()
                self.__parcov.from_parbounds(self.parcov_arg)
            else:
                self.__parcov = self.__fromfile(self.parcov_arg)
        #--if the arg is a pst object
        elif isinstance(self.parcov_arg,pst):
            self.__parcov = cov()
            self.__parcov.from_parameter_data(self.parcov_arg)
        else:
            raise Exception("linear_analysis.__load_parcov(): " +
                            "parcov_arg must be a " +
                            "matrix object or a file name: " +
                            str(self.parcov_arg))
        self.log("loading parcov")


    def __load_obscov(self):
        """private: method to set the obscov attribute from:
                a pest control file (observation weights)
                a pst object
                a matrix object
                an uncert file
                an ascii matrix file
        Args:
            None
        Returns:
            None
        Raises:
            Exception if the obscov_arg is not a matrix object or string
        """
        # if the obscov arg is None, but the pst arg is not None,
        # reset and load from obs weights
        if not self.obscov_arg:
            if self.pst_arg:
                self.obscov_arg = self.pst_arg
            else:
                raise Exception("linear_analysis.__load_obscov(): " +
                                "obscov_arg is None")
        if isinstance(self.obscov_arg,matrix):
            self.__obscov = self.obscov_arg
            return
        if isinstance(self.obscov_arg,np.ndarray):
            # if the ndarray arg is a vector,
            # assume it is the diagonal of the obscov matrix
            if len(self.obscov_arg.shape) == 1:
                assert self.parcov_arg.shape[0] == self.jco.shape[1]
                isdiagonal = True
            else:
                assert self.obscov_arg.shape[0] == self.jco.shape[0]
                assert self.obscov_arg.shape[1] == self.jco.shape[0]
                isdiagonal = False
            self.logger.warn("linear_analysis.__load_obscov(): " +
                             "instantiating obscov from ndarray,  " +
                             "can't verify observation alignment with jco")
            self.__parcov = matrix(x=self.obscov_arg,
                                         isdiagonal=isdiagaonal,
                                         row_names=self.jco.row_names,
                                         col_names=self.jco.row_names)
        self.log("loading obscov")
        if isinstance(self.obscov_arg, str):
            if self.obscov_arg.lower().endswith(".pst"):
                self.__obscov = cov()
                self.__obscov.from_obsweights(self.obscov_arg)
            else:
                self.__obscov = self.__fromfile(self.obscov_arg)
        elif isinstance(self.obscov_arg, pst):
            self.__obscov = cov()
            self.__obscov.from_observation_data(self.obscov_arg)
        else:
            raise Exception("linear_analysis.__load_obscov(): " +
                            "obscov_arg must be a " +
                            "matrix object or a file name: " +
                            str(self.obscov_arg))
        self.log("loading obscov")


    def __load_predictions(self):
        """private: set the predictions attribute from:
                mixed list of row names, matrix files and ndarrays
                a single row name
                an ascii file
            can be none if only interested in parameters.

            linear_analysis.__predictions is stored as a list of column vectors

        Args:
            None
        Returns:
            None
        Raises:
            Assertion error if prediction matrix object is not aligned with
                jco attribute
        """
        if self.prediction_arg is None:
            self.__predictions = None
            return
        self.log("loading forecasts")
        if not isinstance(self.prediction_arg, list):
            self.prediction_arg = [self.prediction_arg]

        row_names = []
        vecs = []
        for arg in self.prediction_arg:
            if isinstance(arg, matrix):
                #--a vector
                if arg.shape[1] == 1:
                    vecs.append(arg)
                else:
                    assert arg.shape[1] == self.jco.shape[1],\
                    "linear_analysis.__load_predictions(): " +\
                    "multi-prediction matrix(npred,npar) not aligned " +\
                    "with jco(nobs,npar): " + str(arg.shape) +\
                    ' ' + str(self.jco.shape)
                    for pred_name in arg.row_names:
                        vecs.append(arg.extract(row_names=pred_name).T)
            elif isinstance(arg, str):
                if arg.lower() in self.jco.row_names:
                    row_names.append(arg.lower())
                else:
                    pred_mat = self.__fromfile(arg)
                    #--vector
                    if pred_mat.shape[1] == 1:
                        vecs.append(pred_mat)
                    else:
                        for pred_name in pred_mat.row_names:
                            vecs.append(pred_mat.get(row_names=pred_name))
            elif isinstance(arg, np.ndarray):
                self.logger.warn("linear_analysis.__load_predictions(): " +
                                "instantiating prediction matrix from " +
                                "ndarray, can't verify alignment")
                self.logger.warn("linear_analysis.__load_predictions(): " +
                                 "instantiating prediction matrix from " +
                                 "ndarray, generating generic prediction names")
                pred_names = []
                [pred_names.append("pred_" + str(i + 1))
                 for i in range(self.prediction_arg.shape[0])]

                if self.jco:
                    names = self.jco.col_names
                elif self.parcov:
                    names = self.parcov.col_names
                else:
                    raise Exception("linear_analysis.__load_predictions(): " +
                                    "ndarray passed for predicitons " +
                                    "requires jco or parcov to get " +
                                    "parameter names")
                pred_matrix = matrix(x=self.prediction_arg,
                                           row_names=pred_names,
                                           col_names=names)
                for pred_name in pred_names:
                    vecs.append(pred_matrix.extract(row_names=pred_name).T)
            else:
                raise Exception("unrecognized predictions argument: " +
                                str(arg))
        if len(row_names) > 0:
            extract = self.jco.extract(row_names=row_names)
            for row_name in row_names:
                vecs.append(extract.get(row_names=row_name).T)

            # call obscov to load __obscov so that __obscov
            # (priavte) can be manipulated
            self.obscov
            self.__obscov.drop(row_names, axis=0)
        self.__predictions = vecs
        self.log("loading forecasts")
        return self.__predictions

    # these property decorators help keep from loading potentially
    # unneeded items until they are called
    # returns a reference - cheap, but can be dangerous


    @property
    def parcov(self):
        if not self.__parcov:
            self.__load_parcov()
        return self.__parcov


    @property
    def obscov(self):
        if not self.__obscov:
            self.__load_obscov()
        return self.__obscov


    @property
    def jco(self):
        if not self.__jco:
            self.__load_jco()
        return self.__jco


    @property
    def predictions(self):
        if not self.__predictions:
            self.__load_predictions()
        return self.__predictions

    @property
    def forecasts(self):
        return self.predictions

    @property
    def pst(self):
        if self.__pst is None and self.pst_arg is None:
            raise Exception("linear_analysis.pst: can't access self.pst:" +
                            "no pest control argument passed")
        elif self.__pst:
            return self.__pst
        else:
            self.__load_pst()


    @property
    def fehalf(self):
        """set the KL parcov scaling matrix attribute
        """
        if self.__fehalf != None:
            return self.__fehalf
        self.log("fehalf")
        self.__fehalf = self.parcov.u * (self.parcov.s ** (0.5))
        self.log("fehalf")
        return self.__fehalf


    @property
    def qhalf(self):
        """set the square root of the cofactor matrix attribute
        """
        if self.__qhalf != None:
            return self.__qhalf
        self.log("qhalf")
        self.__qhalf = self.obscov ** (-0.5)
        self.log("qhalf")
        return self.__qhalf

    @property
    def qhalfx(self):
        if self.__qhalfx is None:
            self.log("qhalfx")
            self.__qhalfx = self.qhalf * self.jco
            self.log("qhalfx")
        return self.__qhalfx

    @property
    def xtqx(self):
        if self.__xtqx is None:
            self.log("xtqx")
            self.__xtqx = self.jco.T * (self.obscov ** -1) * self.jco
            self.log("xtqx")
        return self.__xtqx


    @property
    def prior_parameter(self):
        return self.parcov

    @property
    def prior_forecast(self):
        return self.prior_prediction

    @property
    def prior_prediction(self):
        """get a dict of prior prediction variances
        Args:
            None
        Returns
            dict{prediction name(str):prior variance(float)}
        Raises:
            None
        """
        if self.__prior_prediction is not None:
            return self.__prior_prediction
        else:
            if self.predictions is not None:
                self.log("propagating prior to predictions")
                pred_dict = {}
                for prediction in self.predictions:
                    var = (prediction.T * self.parcov * prediction).x[0, 0]
                    pred_dict[prediction.col_names[0]] = var
                self.__prior_prediction = pred_dict
                self.log("propagating prior to predictions")
            else:
                self.__prior_prediction = {}
            return self.__prior_prediction


    def apply_karhunen_loeve_scaling(self):
        """apply karhuene-loeve scaling to the jacobian matrix.

            This scaling is not necessary for analyses using Schur's
            complement, but can be very important for error variance
            analyses.  This operation effectively transfers prior knowledge
            specified in the parcov to the jacobian and reset parcov to the
            identity matrix.
        Args:
            None
        Returns:
            None
        Raises:
            None
        """
        cnames = copy.deepcopy(self.jco.col_names)
        self.__jco *= self.fehalf
        self.__jco.col_names = cnames
        self.__parcov = self.parcov.identity


    def clean(self):
        """drop regularization and prior information observation from the jco
        """
        if self.pst_arg is None:
            self.logger.warn("linear_analysis.clean(): not pst object")
            return
        if not self.pst.estimation and self.pst.nprior > 0:
            self.drop_prior_information()


    def reset_parcov(self,arg=None):
        """reset the parcov attribute to None
        Args:
            arg (str or matrix) : the value to assign to the parcov_arg attrib
        Returns:
            None
        Raises:
            None
        """
        self.logger.warn("resetting parcov")
        self.__parcov = None
        if arg is not None:
            self.parcov_arg = arg


    def reset_obscov(self,arg=None):
        """reset the obscov attribute to None
        Args:
            arg (str or matrix) : the value to assign to the obscov_arg attrib
        Returns:
            None
        Raises:
            None
        """
        self.logger.warn("resetting obscov")
        self.__obscov = None
        if arg is not None:
            self.obscov_arg = arg


    def drop_prior_information(self):
        """drop the prior information from the jco and pst attributes
        """
        nprior_str = str(self.pst.nprior)
        self.log("removing " + nprior_str + " prior info from jco, pst, and " +
                                            "obs cov")
        #pi_names = list(self.pst.prior_information.pilbl.values)
        pi_names = list(self.pst.prior_names)
        self.__jco.drop(pi_names, axis=0)
        self.__pst.prior_information = self.pst.null_prior
        #self.__obscov.drop(pi_names,axis=0)
        self.log("removing " + nprior_str + " prior info from jco, pst, and " +
                                            "obs cov")


    def get(self,par_names=None,obs_names=None,astype=None):
        """method to get a new linear_analysis class using a
             subset of parameters and/or observations
         Args:
            par_names (enumerable of str) : par names for new object
            obs_names (enumerable of str) : obs names for new object
            astype (either schur or errvar type) : type to cast the new object
        Returns:
            linear_analysis object
        Raises:
            None
        """
        # make sure we aren't fooling with unwanted prior information
        self.clean()
        #--if there is nothing to do but copy
        if par_names is None and obs_names is None:
            if astype is not None:
                self.logger.warn("linear_analysis.get(): astype is not None, " +
                                 "but par_names and obs_names are None so" +
                                 "\n  ->Omitted attributes will not be " +
                                 "propagated to new instance")
            else:
                return copy.deepcopy(self)
        #--make sure the args are lists
        if par_names is not None and not isinstance(par_names, list):
            par_names = [par_names]
        if obs_names is not None and not isinstance(obs_names, list):
            obs_names = [obs_names]

        if par_names is None:
            par_names = self.jco.col_names
        if obs_names is None:
            obs_names = self.jco.row_names
        #--if possible, get a new parcov
        if self.parcov:
            new_parcov = self.parcov.get(col_names=par_names)
        else:
            new_parcov = None
        #--if possible, get a new obscov
        if self.obscov_arg is not None:
            new_obscov = self.obscov.get(row_names=obs_names)
        else:
            new_obscov = None
        #--if possible, get a new pst
        if self.pst_arg is not None:
            new_pst = self.pst.get(par_names=par_names,obs_names=obs_names)
        else:
            new_pst = None
        if self.predictions:
            new_preds = []
            for prediction in self.predictions:
                new_preds.append(prediction.get(row_names=par_names))
        else:
            new_preds = None
        if self.jco_arg is not None:
            new_jco = self.jco.get(row_names=obs_names, col_names=par_names)
        else:
            new_jco = None
        if astype is not None:
            return astype(jco=new_jco, pst=new_pst, parcov=new_parcov,
                          obscov=new_obscov, predictions=new_preds,
                          verbose=False)
        else:
            #--return a new object of the same type
            return type(self)(jco=new_jco, pst=new_pst, parcov=new_parcov,
                              obscov=new_obscov, predictions=new_preds,
                              verbose=False)


    def adjust_obscov_resfile(self, resfile=None):
        """reset the elements of obscov by scaling the implied weights
        based on the phi components in res_file
        """
        self.pst.adjust_weights_resfile(resfile)
        self.__obscov.from_observation_data(self.pst)


    @staticmethod
    def test():
        raise NotImplementedError()













if __name__ == "__main__":
    linear_analysis.test()