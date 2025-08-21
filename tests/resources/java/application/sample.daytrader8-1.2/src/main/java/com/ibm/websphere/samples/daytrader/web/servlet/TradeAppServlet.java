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
package com.ibm.websphere.samples.daytrader.web.servlet;

import java.io.IOException;

import javax.inject.Inject;
import javax.servlet.ServletConfig;
import javax.servlet.ServletContext;
import javax.servlet.ServletException;
import javax.servlet.annotation.WebServlet;
import javax.servlet.http.HttpServlet;
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;
import javax.servlet.http.HttpSession;
import javax.servlet.http.PushBuilder;

import com.ibm.websphere.samples.daytrader.interfaces.Trace;
import com.ibm.websphere.samples.daytrader.util.Log;
import com.ibm.websphere.samples.daytrader.util.TradeConfig;


/**
 *
 * TradeAppServlet provides the standard web interface to Trade and can be
 * accessed with the Go Trade! link. Driving benchmark load using this interface
 * requires a sophisticated web load generator that is capable of filling HTML
 * forms and posting dynamic data.
 */

@WebServlet(name = "TradeAppServlet", urlPatterns = { "/app" })
@Trace
public class TradeAppServlet extends HttpServlet {

  @Inject
  TradeServletAction tsAction; 
  
  private static final long serialVersionUID = 481530522846648373L;

  /**
   * Servlet initialization method.
   */
  @Override
  public void init(ServletConfig config) throws ServletException {
    super.init(config);
    java.util.Enumeration<String> en = config.getInitParameterNames();
    while (en.hasMoreElements()) {
      String parm = en.nextElement();
      String value = config.getInitParameter(parm);
      TradeConfig.setConfigParam(parm, value);
    }
    try {
      // TODO: Uncomment this once split-tier issue is resolved
      // TradeDirect.init();
    } catch (Exception e) {
      Log.error(e, "TradeAppServlet:init -- Error initializing TradeDirect");
    }
  }

  /**
   * Returns a string that contains information about TradeScenarioServlet
   *
   * @return The servlet information
   */
  @Override
  public java.lang.String getServletInfo() {
    return "TradeAppServlet provides the standard web interface to Trade";
  }

  /**
   * Process incoming HTTP GET requests
   *
   * @param request
   *            Object that encapsulates the request to the servlet
   * @param response
   *            Object that encapsulates the response from the servlet
   */
  @Override
  public void doGet(javax.servlet.http.HttpServletRequest request, javax.servlet.http.HttpServletResponse response) throws ServletException, IOException {
    performTask(request, response);
  }

  /**
   * Process incoming HTTP POST requests
   *
   * @param request
   *            Object that encapsulates the request to the servlet
   * @param response
   *            Object that encapsulates the response from the servlet
   */
  @Override
  public void doPost(javax.servlet.http.HttpServletRequest request, javax.servlet.http.HttpServletResponse response) throws ServletException, IOException {
    performTask(request, response);
  }

  /**
   * Main service method for TradeAppServlet
   *
   * @param request
   *            Object that encapsulates the request to the servlet
   * @param response
   *            Object that encapsulates the response from the servlet
   */
  public void performTask(HttpServletRequest req, HttpServletResponse resp) throws ServletException, IOException {

    String action = null;
    String userID = null;
    // String to create full dispatch path to TradeAppServlet w/ request
    // Parameters

    resp.setContentType("text/html");

    // Dyna - need status string - prepended to output
    action = req.getParameter("action");

    ServletContext ctx = getServletConfig().getServletContext();

    if (action == null) {
      tsAction.doWelcome(ctx, req, resp, "");
      return;
    } else if (action.equals("login")) {
      userID = req.getParameter("uid");
      String passwd = req.getParameter("passwd");
      tsAction.doLogin(ctx, req, resp, userID, passwd);
      return;
    } else if (action.equals("register")) {
      userID = req.getParameter("user id");
      String passwd = req.getParameter("passwd");
      String cpasswd = req.getParameter("confirm passwd");
      String fullname = req.getParameter("Full Name");
      String ccn = req.getParameter("Credit Card Number");
      String money = req.getParameter("money");
      String email = req.getParameter("email");
      String smail = req.getParameter("snail mail");
      tsAction.doRegister(ctx, req, resp, userID, passwd, cpasswd, fullname, ccn, money, email, smail);
      return;
    }

    // The rest of the operations require the user to be logged in -
    // Get the Session and validate the user.
    HttpSession session = req.getSession();
    userID = (String) session.getAttribute("uidBean");

    if (userID == null) {
      System.out.println("TradeAppServlet service error: User Not Logged in");
      tsAction.doWelcome(ctx, req, resp, "User Not Logged in");
      return;
    }
    
    // try http/2 push if we get here
    // should be logged in and doing real work by this point
    if (!action.equals("logout") && TradeConfig.getWebInterface() == TradeConfig.JSP_Images_HTTP2) {
      pushHeaderImages(req.newPushBuilder());
    }
    
    if (action.equals("quotes")) {
      String symbols = req.getParameter("symbols");
      tsAction.doQuotes(ctx, req, resp, userID, symbols);
    } else if (action.equals("buy")) {
      String symbol = req.getParameter("symbol");
      String quantity = req.getParameter("quantity");
      tsAction.doBuy(ctx, req, resp, userID, symbol, quantity);
    } else if (action.equals("sell")) {
      int holdingID = Integer.parseInt(req.getParameter("holdingID"));
      tsAction.doSell(ctx, req, resp, userID, new Integer(holdingID));
    } else if (action.equals("portfolio") || action.equals("portfolioNoEdge")) {
      tsAction.doPortfolio(ctx, req, resp, userID, "Portfolio as of " + new java.util.Date());
    } else if (action.equals("logout")) {
      tsAction.doLogout(ctx, req, resp, userID);
    } else if (action.equals("home")) {
      tsAction.doHome(ctx, req, resp, userID, "Ready to Trade");
    } else if (action.equals("account")) {
      tsAction.doAccount(ctx, req, resp, userID, "");
    } else if (action.equals("update_profile")) {      
      String password = req.getParameter("password");
      String cpassword = req.getParameter("cpassword");
      String fullName = req.getParameter("fullname");
      String address = req.getParameter("address");
      String creditcard = req.getParameter("creditcard");
      String email = req.getParameter("email");
      tsAction.doAccountUpdate(ctx, req, resp, userID, password == null ? "" : password.trim(), cpassword == null ? "" : cpassword.trim(),
          fullName == null ? "" : fullName.trim(), address == null ? "" : address.trim(), creditcard == null ? "" : creditcard.trim(),
              email == null ? "" : email.trim());
    } else if (action.equals("mksummary")) {
      tsAction.doMarketSummary(ctx, req, resp, userID);
    } else {
      System.out.println("TradeAppServlet: Invalid Action=" + action);
      tsAction.doWelcome(ctx, req, resp, "TradeAppServlet: Invalid Action" + action);
    }
  }

  private void pushHeaderImages(PushBuilder pushBuilder) {       
    if (pushBuilder != null) {
      pushBuilder.path("images/menuHome.gif").addHeader("content-type", "image/gif").push();
      pushBuilder.path("images/account.gif").addHeader("content-type", "image/gif").push();
      pushBuilder.path("images/portfolio.gif").addHeader("content-type", "image/gif").push();
      pushBuilder.path("images/quotes.gif").addHeader("content-type", "image/gif").push();
      pushBuilder.path("images/logout.gif").addHeader("content-type", "image/gif").push();
      pushBuilder.path("images/graph.gif").addHeader("content-type", "image/gif").push();
      pushBuilder.path("images/line.gif").addHeader("content-type", "image/gif").push();
      Log.trace("HTTP/2 is enabled");
    } else {
      Log.error("HTTP/2 not enabled");
    }
    
  }

}