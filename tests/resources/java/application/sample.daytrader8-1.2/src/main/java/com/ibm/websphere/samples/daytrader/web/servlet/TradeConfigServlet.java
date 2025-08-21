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
import javax.servlet.ServletException;
import javax.servlet.annotation.WebServlet;
import javax.servlet.http.HttpServlet;
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;


import com.ibm.websphere.samples.daytrader.beans.RunStatsDataBean;
import com.ibm.websphere.samples.daytrader.impl.direct.TradeDirectDBUtils;
import com.ibm.websphere.samples.daytrader.interfaces.Trace;
import com.ibm.websphere.samples.daytrader.util.Log;
import com.ibm.websphere.samples.daytrader.util.TradeConfig;


/**
 * TradeConfigServlet provides a servlet interface to adjust DayTrader runtime parameters.
 * TradeConfigServlet updates values in the {@link com.ibm.websphere.samples.daytrader.web.TradeConfig} JavaBean holding
 * all configuration and runtime parameters for the Trade application
 *
 */
@WebServlet(name = "TradeConfigServlet", urlPatterns = { "/config" })
@Trace
public class TradeConfigServlet extends HttpServlet {  

  @Inject
  private TradeDirectDBUtils dbUtils;

  private static final long serialVersionUID = -1910381529792500095L;

  /**
   * Servlet initialization method.
   */
  @Override
  public void init(ServletConfig config) throws ServletException {
    super.init(config);
  }

  /**
   * Create the TradeConfig bean and pass it the config.jsp page
   * to display the current Trade runtime configuration
   * Creation date: (2/8/2000 3:43:59 PM)
   */
  void doConfigDisplay(HttpServletRequest req, HttpServletResponse resp, String results) throws Exception {

    TradeConfig currentConfig = new TradeConfig();

    req.setAttribute("tradeConfig", currentConfig);
    req.setAttribute("status", results);
    getServletConfig().getServletContext().getRequestDispatcher(TradeConfig.getPage(TradeConfig.CONFIG_PAGE)).include(req, resp);
  }

  void doResetTrade(HttpServletRequest req, HttpServletResponse resp, String results) throws Exception {
    RunStatsDataBean runStatsData = new RunStatsDataBean();
    TradeConfig currentConfig = new TradeConfig();

    try {
      runStatsData = dbUtils.resetTrade(false);

      req.setAttribute("runStatsData", runStatsData);
      req.setAttribute("tradeConfig", currentConfig);
      results += "Trade Reset completed successfully";
      req.setAttribute("status", results);

    } catch (Exception e) {
      results += "Trade Reset Error  - see log for details";
      Log.error(e, results);
      throw e;
    }
    getServletConfig().getServletContext().getRequestDispatcher(TradeConfig.getPage(TradeConfig.STATS_PAGE)).include(req, resp);

  }

  /**
   * Update Trade runtime configuration paramaters
   * Creation date: (2/8/2000 3:44:24 PM)
   */
  void doConfigUpdate(HttpServletRequest req, HttpServletResponse resp) throws Exception {
    String currentConfigStr = "\n\n########## Trade configuration update. Current config:\n\n";

    currentConfigStr += "\t\tRuntimeMode:\t\t" + TradeConfig.getRunTimeModeNames()[TradeConfig.getRunTimeMode()] + "\n";
    
    String orderProcessingModeStr = req.getParameter("OrderProcessingMode");
    if (orderProcessingModeStr != null) {
      try {
        int i = Integer.parseInt(orderProcessingModeStr);
        if ((i >= 0) && (i < TradeConfig.getOrderProcessingModeNames().length)) //Input validation
          TradeConfig.setOrderProcessingMode(i);
      } catch (Exception e) {
        //>>rjm
        Log.error(e, "TradeConfigServlet.doConfigUpdate(..): minor exception caught", "trying to set orderProcessing to " + orderProcessingModeStr,
            "reverting to current value");

      } // If the value is bad, simply revert to current
    }
    currentConfigStr += "\t\tOrderProcessingMode:\t\t" + TradeConfig.getOrderProcessingModeNames()[TradeConfig.getOrderProcessingMode()] + "\n";

    String webInterfaceStr = req.getParameter("WebInterface");
    if (webInterfaceStr != null) {
      try {
        int i = Integer.parseInt(webInterfaceStr);
        if ((i >= 0) && (i < TradeConfig.getWebInterfaceNames().length)) //Input validation
          TradeConfig.setWebInterface(i);
      } catch (Exception e) {
        Log.error(e, "TradeConfigServlet.doConfigUpdate(..): minor exception caught", "trying to set WebInterface to " + webInterfaceStr,
            "reverting to current value");

      } // If the value is bad, simply revert to current
    }
    currentConfigStr += "\t\tWeb Interface:\t\t\t" + TradeConfig.getWebInterfaceNames()[TradeConfig.getWebInterface()] + "\n";

    String parm = req.getParameter("MaxUsers");
    if ((parm != null) && (parm.length() > 0)) {
      try {
        TradeConfig.setMAX_USERS(Integer.parseInt(parm));
      } catch (Exception e) {
        Log.error(e, "TradeConfigServlet.doConfigUpdate(..): minor exception caught", "Setting maxusers, probably error parsing string to int:" + parm,
            "revertying to current value: " + TradeConfig.getMAX_USERS());

      } //On error, revert to saved
    }
    parm = req.getParameter("MaxQuotes");
    if ((parm != null) && (parm.length() > 0)) {
      try {
        TradeConfig.setMAX_QUOTES(Integer.parseInt(parm));
      } catch (Exception e) {
        //>>rjm
        Log.error(e, "TradeConfigServlet: minor exception caught", "trying to set max_quotes, error on parsing int " + parm,
            "reverting to current value " + TradeConfig.getMAX_QUOTES());
        //<<rjm

      } //On error, revert to saved
    }
    currentConfigStr += "\t\tTrade Users:\t\t\t" + TradeConfig.getMAX_USERS() + "\n";
    currentConfigStr += "\t\tTrade Quotes:\t\t\t" + TradeConfig.getMAX_QUOTES() + "\n";

    parm = req.getParameter("marketSummaryInterval");
    if ((parm != null) && (parm.length() > 0)) {
      try {
        TradeConfig.setMarketSummaryInterval(Integer.parseInt(parm));
      } catch (Exception e) {
        Log.error(e, "TradeConfigServlet: minor exception caught", "trying to set marketSummaryInterval, error on parsing int " + parm,
            "reverting to current value " + TradeConfig.getMarketSummaryInterval());

      }
    }
    currentConfigStr += "\t\tMarket Summary Interval:\t" + TradeConfig.getMarketSummaryInterval() + "\n";

    parm = req.getParameter("primIterations");
    if ((parm != null) && (parm.length() > 0)) {
      try {
        TradeConfig.setPrimIterations(Integer.parseInt(parm));
      } catch (Exception e) {
        Log.error(e, "TradeConfigServlet: minor exception caught", "trying to set primIterations, error on parsing int " + parm,
            "reverting to current value " + TradeConfig.getPrimIterations());

      }
    }
    currentConfigStr += "\t\tPrimitive Iterations:\t\t" + TradeConfig.getPrimIterations() + "\n";

    String enablePublishQuotePriceChange = req.getParameter("EnablePublishQuotePriceChange");

    if (enablePublishQuotePriceChange != null)
      TradeConfig.setPublishQuotePriceChange(true);
    else
      TradeConfig.setPublishQuotePriceChange(false);
    currentConfigStr += "\t\tTradeStreamer MDB Enabled:\t" + TradeConfig.getPublishQuotePriceChange() + "\n";

    parm = req.getParameter("ListQuotePriceChangeFrequency");
    if ((parm != null) && (parm.length() > 0)) {
      try {
        TradeConfig.setListQuotePriceChangeFrequency(Integer.parseInt(parm));
      } catch (Exception e) {
        Log.error(e, "TradeConfigServlet: minor exception caught", "trying to set percentSentToWebSocket, error on parsing int " + parm,
            "reverting to current value " + TradeConfig.getListQuotePriceChangeFrequency());

      }
    }
    currentConfigStr += "\t\t% of trades on Websocket:\t" + TradeConfig.getListQuotePriceChangeFrequency() + "\n";

    String enableLongRun = req.getParameter("EnableLongRun");

    if (enableLongRun != null)
      TradeConfig.setLongRun(true);
    else
      TradeConfig.setLongRun(false);
    currentConfigStr += "\t\tLong Run Enabled:\t\t" + TradeConfig.getLongRun() + "\n";

    String displayOrderAlerts = req.getParameter("DisplayOrderAlerts");

    if (displayOrderAlerts != null)
      TradeConfig.setDisplayOrderAlerts(true);
    else
      TradeConfig.setDisplayOrderAlerts(false);
    currentConfigStr += "\t\tDisplay Order Alerts:\t\t" + TradeConfig.getDisplayOrderAlerts() + "\n";

    System.out.println(currentConfigStr);
  }

  @Override
  public void service(HttpServletRequest req, HttpServletResponse resp) throws ServletException, IOException {

    String action = null;
    String result = "";

    resp.setContentType("text/html");
    try {
      action = req.getParameter("action");
      if (action == null) {
        doConfigDisplay(req, resp, result + "<b><br>Current DayTrader Configuration:</br></b>");
        return;
      } else if (action.equals("updateConfig")) {
        doConfigUpdate(req, resp);
        result = "<B><BR>DayTrader Configuration Updated</BR></B>";
      } else if (action.equals("resetTrade")) {
        doResetTrade(req, resp, "");
        return;
      } else if (action.equals("buildDB")) {
        resp.setContentType("text/html");
        dbUtils.buildDB(resp.getWriter(), null);
        result = "DayTrader Database Built - " + TradeConfig.getMAX_USERS() + "users created";
      } else if (action.equals("buildDBTables")) {

        resp.setContentType("text/html");

        String dbProductName = null;
        try {
          dbProductName = dbUtils.checkDBProductName();
        } catch (Exception e) {
          Log.error(e, "TradeBuildDB: Unable to check DB Product name");
        }
        if (dbProductName == null) {
          resp.getWriter().println(
              "<BR>TradeBuildDB: **** Unable to check DB Product name, please check Database/AppServer configuration and retry ****</BR></BODY>");
          return;
        }

        String ddlFile = null;
        //Locate DDL file for the specified database
        try {
          resp.getWriter().println("<BR>TradeBuildDB: **** Database Product detected: " + dbProductName + " ****</BR>");
          if (dbProductName.startsWith("DB2/")) {// if db is DB2
            ddlFile = "/dbscripts/db2/Table.ddl";
          } else if (dbProductName.startsWith("Apache Derby")) { //if db is Derby
            ddlFile = "/dbscripts/derby/Table.ddl";
          } else if (dbProductName.startsWith("Oracle")) { // if the Db is Oracle
            ddlFile = "/dbscripts/oracle/Table.ddl";
          } else {// Unsupported "Other" Database
            ddlFile = "/dbscripts/other/Table.ddl";
            resp.getWriter().println("<BR>TradeBuildDB: **** This Database is unsupported/untested use at your own risk ****</BR>");
          }

          resp.getWriter().println("<BR>TradeBuildDB: **** The DDL file at path <I>" + ddlFile + "</I> will be used ****</BR>");
          resp.getWriter().flush();
        } catch (Exception e) {
          Log.error(e, "TradeBuildDB: Unable to locate DDL file for the specified database");
          resp.getWriter().println("<BR>TradeBuildDB: **** Unable to locate DDL file for the specified database ****</BR></BODY>");
          return;
        }

        dbUtils.buildDB(resp.getWriter(), getServletContext().getResourceAsStream(ddlFile));

      }
      doConfigDisplay(req, resp, result + "Current DayTrader Configuration:");
    } catch (Exception e) {
      Log.error(e, "TradeConfigServlet.service(...)", "Exception trying to perform action=" + action);

      resp.sendError(500, "TradeConfigServlet.service(...)" + "Exception trying to perform action=" + action + "\nException details: " + e.toString());

    }
  }
}
