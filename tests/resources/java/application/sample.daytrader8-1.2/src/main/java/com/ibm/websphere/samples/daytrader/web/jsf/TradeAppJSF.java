/**
 * (C) Copyright IBM Corporation 2015.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */
package com.ibm.websphere.samples.daytrader.web.jsf;

import java.io.Serializable;
import java.math.BigDecimal;

import javax.enterprise.context.SessionScoped;
import javax.enterprise.inject.Any;
import javax.enterprise.inject.Instance;
import javax.faces.context.ExternalContext;
import javax.inject.Inject;
import javax.inject.Named;
import javax.servlet.ServletException;
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpSession;
import javax.validation.constraints.Email;
import javax.validation.constraints.NotBlank;

import com.ibm.websphere.samples.daytrader.entities.AccountDataBean;
import com.ibm.websphere.samples.daytrader.entities.AccountProfileDataBean;
import com.ibm.websphere.samples.daytrader.interfaces.Trace;
import com.ibm.websphere.samples.daytrader.interfaces.TradeServices;
import com.ibm.websphere.samples.daytrader.util.Log;
import com.ibm.websphere.samples.daytrader.util.TradeConfig;
import com.ibm.websphere.samples.daytrader.util.TradeRunTimeModeLiteral;

@Named("tradeapp")
@SessionScoped
@Trace
public class TradeAppJSF implements Serializable {

  @Inject ExternalContext context;

  private TradeServices tradeAction;

  private static final long serialVersionUID = 2L;
  
  @NotBlank
  private String userID = "uid:0";
  
  @NotBlank
  private String password = "xxx";
  
  @NotBlank
  private String cpassword;
  
  @NotBlank
  private String results;
  
  @NotBlank
  private String fullname;
  
  @NotBlank
  private String address;
  
  @Email
  private String email;
  
  @NotBlank
  private String ccn;
  
  @NotBlank
  private String money;

  @Inject 
  public TradeAppJSF(@Any Instance<TradeServices> services) {
    tradeAction = services.select(new TradeRunTimeModeLiteral(TradeConfig.getRunTimeModeNames()[TradeConfig.getRunTimeMode()])).get();
  }

  public String login() {
    try {        	
      AccountDataBean accountData = tradeAction.login(userID, password);

      AccountProfileDataBean accountProfileData = tradeAction.getAccountProfileData(userID);
      if (accountData != null) {
        HttpSession session = (HttpSession) context.getSession(true);

        session.setAttribute("uidBean", userID);
        session.setAttribute("sessionCreationDate", new java.util.Date());
        setResults("Ready to Trade");

        // Get account profile information
        setAddress(accountProfileData.getAddress());
        setCcn(accountProfileData.getCreditCard());
        setEmail(accountProfileData.getEmail());
        setFullname(accountProfileData.getFullName());
        setCpassword(accountProfileData.getPassword());
        return "Ready to Trade";
      } else {
        Log.log("TradeServletAction.doLogin(...)", "Error finding account for user " + userID + "",
            "user entered a bad username or the database is not populated");
        throw new NullPointerException("User does not exist or password is incorrect!");
      }
    }

    catch (Exception se) {
      // Go to welcome page
      setResults("Could not find account");
      return "welcome";
    }
  }

  public String register() {

    // Validate user passwords match and are atleast 1 char in length
    try {
      if ((password.equals(cpassword)) && (password.length() >= 1)) {
        AccountDataBean accountData = tradeAction.register(userID, password, fullname, address, email, ccn, new BigDecimal(money));

        if (accountData == null) {
          setResults("Registration operation failed;");
          // Go to register page
          return "Registration operation failed";

        } else {
          login();
          setResults("Registration operation succeeded;  Account " + accountData.getAccountID() + " has been created.");
          return "Registration operation succeeded";
        }
      }

      else {
        // Password validation failed
        setResults("Registration operation failed, your passwords did not match");
        // Go to register page
        return "Registration operation failed";
      }
    }

    catch (Exception e) {
      // log the exception with error page
      Log.log("TradeServletAction.doRegister(...)" + " exception user =" + userID);
      try {
        throw new Exception("TradeServletAction.doRegister(...)" + " exception user =" + userID, e);
      } catch (Exception e1) {
        e1.printStackTrace();
      }

    }
    return "Registration operation succeeded";
  }

  public String updateProfile() {

    // First verify input data
    boolean doUpdate = true;

    if (password.equals(cpassword) == false) {
      results = "Update profile error: passwords do not match";
      doUpdate = false;
    }

    AccountProfileDataBean accountProfileData = new AccountProfileDataBean(userID, password, fullname, address, email, ccn);

    try {
      if (doUpdate) {
        accountProfileData = tradeAction.updateAccountProfile(accountProfileData);
        results = "Account profile update successful";
      }

    } catch (java.lang.IllegalArgumentException e) {
      // this is a user error so I will
      // forward them to another page rather than throw a 500
      setResults("invalid argument, check userID is correct, and the database is populated" + userID);
      Log.error(e, "TradeServletAction.doAccount(...)", "illegal argument, information should be in exception string",
          "treating this as a user error and forwarding on to a new page");
    } catch (Exception e) {
      // log the exception with error page
      e.printStackTrace();
    }
    // Go to account.xhtml
    return "Go to account";
  }

  public String logout() {

    try {
      setResults("");
      tradeAction.logout(userID);
    } catch (java.lang.IllegalArgumentException e) {
      // this is a user error so I will
      // forward them to another page, at the end of the page.
      setResults("illegal argument:" + e.getMessage());

      // log the exception with an error level of 3 which means, handled
      // exception but would invalidate a automation run
      Log.error(e, "TradeServletAction.doLogout(...)", "illegal argument, information should be in exception string",
          "treating this as a user error and forwarding on to a new page");
    } catch (Exception e) {
      // log the exception and foward to a error page
      Log.error(e, "TradeAppJSF.logout():", "Error logging out" + userID, "fowarding to an error page");
    }

    HttpSession session = (HttpSession)context.getSession(false);

    if (session != null) {
      session.invalidate();
    }

    // Added to actually remove a user from the authentication cache
    try {
      ((HttpServletRequest) context.getRequest()).logout();
    } catch (ServletException e) {
      Log.error(e, "TradeAppJSF.logout():", "Error logging out request" + userID, "fowarding to an error page");
    }

    // Go to welcome page
    return "welcome";
  }

  public String getUserID() {
    return userID;
  }

  public void setUserID(String userID) {
    this.userID = userID;
  }

  public String getPassword() {
    return password;
  }

  public void setPassword(String password) {
    this.password = password;
  }

  public String getCpassword() {
    return cpassword;
  }

  public void setCpassword(String cpassword) {
    this.cpassword = cpassword;
  }

  public String getFullname() {
    return fullname;
  }

  public void setFullname(String fullname) {
    this.fullname = fullname;
  }

  public String getResults() {
    String tempResults=results;
    results="";
    return tempResults;
  }

  public void setResults(String results) {
    this.results = results;
  }

  public String getAddress() {
    return address;
  }

  public void setAddress(String address) {
    this.address = address;
  }

  public String getEmail() {
    return email;
  }

  public void setEmail(String email) {
    this.email = email;
  }

  public String getCcn() {
    return ccn;
  }

  public void setCcn(String ccn) {
    this.ccn = ccn;
  }

  public String getMoney() {
    return money;
  }

  public void setMoney(String money) {
    this.money = money;
  }
};
