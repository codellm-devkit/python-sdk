/**
 * (C) Copyright IBM Corporation 2019.
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
package com.ibm.websphere.samples.daytrader.web.prims.beanval;

import java.io.IOException;
import java.time.LocalDateTime;

import javax.servlet.ServletConfig;
import javax.servlet.ServletException;
import javax.servlet.ServletOutputStream;
import javax.servlet.annotation.WebServlet;
import javax.servlet.http.HttpServlet;
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;

import com.ibm.websphere.samples.daytrader.util.Log;

@WebServlet(name = "PingServletBeanValSimple2", urlPatterns = { "/servlet/PingServletBeanValSimple2" })
public class PingServletBeanValSimple2  extends HttpServlet {

  private static final long serialVersionUID = 7097023236709683760L;
  private static LocalDateTime initTime;
  private static int hitCount = 0;


  /**
   * forwards post requests to the doGet method Creation date: (11/6/2000
   * 10:52:39 AM)
   *
   * @param res
   *            javax.servlet.http.HttpServletRequest
   * @param res2
   *            javax.servlet.http.HttpServletResponse
   */
  @Override
  public void doPost(HttpServletRequest req, HttpServletResponse res) throws ServletException, IOException {
    doGet(req, res);
  }

  /**
   * this is the main method of the servlet that will service all get
   * requests.
   *
   * @param request
   *            HttpServletRequest
   * @param responce
   *            HttpServletResponce
   **/
  @Override
  public void doGet(HttpServletRequest req, HttpServletResponse res) throws ServletException, IOException {
    try {            
      res.setContentType("text/html");

      SimpleBean2 simpleBean2 = new SimpleBean2();
      simpleBean2.checkInjectionValidation();

      ServletOutputStream out = res.getOutputStream();

      int currentHitCount = ++hitCount;
      out.println("<html><head><title>Ping Servlet Bean Validation Simple</title></head>"
          + "<body><HR><BR><FONT size=\"+2\" color=\"#000066\">Ping Servlet Bean Validation Simple<BR></FONT><FONT size=\"+1\" color=\"#000066\">Init time : " + initTime
          + "<BR><BR></FONT>  <B>Hit Count: " + currentHitCount + "</B></body></html>");
    } catch (Exception e) {
      Log.error(e, "PingServlet.doGet(...): general exception caught");
      res.sendError(500, e.toString());

    }
  }

  /**
   * returns a string of information about the servlet
   *
   * @return info String: contains info about the servlet
   **/
  @Override
  public String getServletInfo() {
    return "Basic dynamic HTML generation through a servlet";
  }

  /**
   * called when the class is loaded to initialize the servlet
   *
   * @param config
   *            ServletConfig:
   **/
  @Override
  public void init(ServletConfig config) throws ServletException {
    super.init(config);
    initTime = LocalDateTime.now();


  }
}
